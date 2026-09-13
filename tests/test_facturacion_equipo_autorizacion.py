"""Nº de autorización por integrante del equipo quirúrgico.

`detalle_facturacion` ya guarda una fila por integrante del equipo
(`grupo_equipo_id` + `tpo_funcion`), y `autorizacion` ya es una columna de esa
fila — no de la cabecera. La API acepta el campo por ítem desde antes de este
test (`PrestacionItem.autorizacion` en `schemas.py`, persistido tal cual en
`service.py::_insertar_prestaciones`); lo único que faltaba era la cobertura.

Corre contra la base de desarrollo (`coleg185_anexo`, ver `conftest.py`), pero
con datos 100% sintéticos: un `cod_obra` y tres médicos ficticios que el propio
test crea y borra, para no tocar médicos ni facturas reales. El período se
resuelve con el mecanismo real (`get_periodo_activo` = último cerrado + 1),
sembrando una cabecera cerrada en un período viejo que no colisiona con nada.

El setup/teardown usa `pymysql` SÍNCRONO en vez de la sesión async del `db`
fixture: mezclar dos engines async (el del fixture y el de la app, usado
adentro de `TestClient`) dispara un bug de event loop de este entorno
(Windows + ProactorEventLoop) ajeno a esta funcionalidad — el mismo que ya
revienta `test_facturacion_ficha.py` al segundo request contra la DB. Yendo
síncrono para el setup, el único engine async en juego es el de la app.
"""
import datetime

import pymysql
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_access_token

COD_OBRA_TEST = "29999"  # no existe en el catálogo real de obras sociales
NRO_CIRUJANO = 999990001
NRO_AYUDANTE_1 = 999990002
NRO_AYUDANTE_2 = 999990003
PERIODO_SEED = "202001"  # cabecera "cerrada" ficticia: bootstrapea el período automático


def _conn():
    return pymysql.connect(
        host=settings.MYSQL_HOST, port=settings.MYSQL_PORT or 3306,
        user=settings.MYSQL_USER, password=settings.MYSQL_PASS, database=settings.MYSQL_DB,
        autocommit=True,
    )


@pytest.fixture
def cliente(app):
    # `with` mantiene UN solo portal/event loop para toda la cadena de requests del
    # test: sin él, cada llamada abre y cierra su propio loop y el pool de conexiones
    # del engine de la app (module-level, no NullPool) termina reusando en la Nda
    # llamada una conexión atada al loop ya muerto de una llamada anterior — revienta
    # con "attached to a different loop" a partir de la 3ra request de la cadena.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def headers():
    token = create_access_token(
        sub="1", uid=1, scopes=["facturacion:cargar", "facturacion:leer"], role="admin",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def equipo_sintetico():
    """Médicos ficticios + una cabecera cerrada vieja (para que `get_periodo_activo`
    resuelva sin depender de facturas reales). Al final borra todo lo que haya quedado
    bajo `COD_OBRA_TEST` (detalle + cabeceras) y los tres médicos ficticios."""
    con = _conn()
    try:
        with con.cursor() as cur:
            for nro in (NRO_CIRUJANO, NRO_AYUDANTE_1, NRO_AYUDANTE_2):
                cur.execute(
                    "INSERT INTO listado_medico (NRO_SOCIO, EXISTE, hashed_password, conceps_espec) "
                    "VALUES (%s, %s, %s, %s)",
                    (nro, "S", "x", '{"conceps": [], "espec": []}'),
                )
            cur.execute(
                "INSERT INTO facturacion "
                "(id_cliente, tipo_factura, nro_factura, tipo_factura_2, nro_factura_2, "
                " tipo_factura_3, nro_factura_3, periodo, cod_obr, fecha, fecha_envio, "
                " fecha_recep, importe, afip, usuario, estado, estado_doctor, version) "
                "VALUES (0, '', '', '', '', '', '', %s, %s, %s, %s, %s, 0, 'N', '1', 'C', 'C', 1)",
                (PERIODO_SEED, COD_OBRA_TEST, datetime.date(2020, 1, 1),
                 datetime.date(2020, 1, 1), datetime.date(2020, 1, 1)),
            )
        yield
    finally:
        with con.cursor() as cur:
            cur.execute("DELETE FROM detalle_facturacion WHERE cod_obr = %s", (COD_OBRA_TEST,))
            cur.execute("DELETE FROM facturacion WHERE cod_obr = %s", (COD_OBRA_TEST,))
            cur.execute(
                "DELETE FROM listado_medico WHERE NRO_SOCIO IN (%s, %s, %s)",
                (NRO_CIRUJANO, NRO_AYUDANTE_1, NRO_AYUDANTE_2),
            )
        con.close()


def _item(cod_medico: int, autorizacion: str, *, honorarios=0, ayudante=0) -> dict:
    return {
        "cod_medico": str(cod_medico),
        "cod_medico_ejecutor": None,
        "dni_paciente": None,
        "fecha_practica": None,
        "cod_clinica": None,
        "autorizacion": autorizacion,
        "cod_nomenclador": "999901",
        "via": None,
        "cantidad": 1,
        "sesion": 1,
        "tipo_calculo": "M",
        "honorarios": honorarios,
        "gastos": 0,
        "ayudante": ayudante,
        "porcentaje": 100,
        "grupo_equipo_id": None,
    }


def test_post_equipo_guarda_autorizacion_distinta_por_integrante(cliente, headers, equipo_sintetico):
    payload = {
        "obra_social": COD_OBRA_TEST,
        "prestaciones": [
            _item(NRO_CIRUJANO, "AUT-CIRUJANO", honorarios=100),
            _item(NRO_AYUDANTE_1, "AUT-AYUDANTE-1", ayudante=10),
            _item(NRO_AYUDANTE_2, "AUT-AYUDANTE-2", ayudante=10),
        ],
    }
    r = cliente.post("/api/facturacion/prestaciones", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    ids = r.json()["ids"]
    assert len(ids) == 3

    # El cirujano es el primer ítem con honorarios > 0 → cabeza del equipo.
    cabeza_id = ids[0]

    r2 = cliente.get(f"/api/facturacion/prestaciones/{cabeza_id}", headers=headers)
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["grupo_equipo_id"] == cabeza_id
    todos = [body] + body["grupo"]
    assert len(todos) == 3
    por_autorizacion = {p["cod_medico"]: p["autorizacion"] for p in todos}
    assert por_autorizacion[str(NRO_CIRUJANO)] == "AUT-CIRUJANO"
    assert por_autorizacion[str(NRO_AYUDANTE_1)] == "AUT-AYUDANTE-1"
    assert por_autorizacion[str(NRO_AYUDANTE_2)] == "AUT-AYUDANTE-2"

    # PATCH sobre un ayudante cambia SOLO esa fila — no la del cirujano ni la del otro ayudante.
    id_ayudante_1 = ids[1]
    r3 = cliente.patch(
        f"/api/facturacion/prestaciones/{id_ayudante_1}",
        json={"autorizacion": "AUT-AYUDANTE-1-CORREGIDA"},
        headers=headers,
    )
    assert r3.status_code == 200, r3.text
    assert r3.json()["autorizacion"] == "AUT-AYUDANTE-1-CORREGIDA"

    r4 = cliente.get(f"/api/facturacion/prestaciones/{cabeza_id}", headers=headers)
    assert r4.status_code == 200, r4.text
    todos2 = [r4.json()] + r4.json()["grupo"]
    por_autorizacion2 = {p["cod_medico"]: p["autorizacion"] for p in todos2}
    assert por_autorizacion2[str(NRO_CIRUJANO)] == "AUT-CIRUJANO"
    assert por_autorizacion2[str(NRO_AYUDANTE_1)] == "AUT-AYUDANTE-1-CORREGIDA"
    assert por_autorizacion2[str(NRO_AYUDANTE_2)] == "AUT-AYUDANTE-2"
