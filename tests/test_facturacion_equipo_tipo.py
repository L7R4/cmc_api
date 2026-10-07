"""`tipo` de los integrantes de un equipo quirúrgico.

Sólo la fila de la cabeza sabe si la clínica fue el prestador (`tipo='Sanatorio'`): el ayudante
se carga como médico, sin clínica, y por su cuenta su `tipo` saldría por la categoría del código
(Práctica/Consulta). Todo integrante tiene que guardarse con el MISMO tipo que su cabeza. Un
ayudante cargado SIN equipo es Honorarios individuales.

Misma estrategia que `test_facturacion_equipo_autorizacion.py`: datos 100% sintéticos (cod_obra
y médicos ficticios que el propio test crea y borra, `pymysql` síncrono para el setup).
"""
import datetime

import pymysql
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_access_token

COD_OBRA_TEST = "29998"  # no existe en el catálogo real de obras sociales
NRO_CLINICA = 999990014
NRO_CIRUJANO = 999990011
NRO_AYUDANTE = 999990012
PERIODO_SEED = "202001"


def _conn():
    return pymysql.connect(
        host=settings.MYSQL_HOST, port=settings.MYSQL_PORT or 3306,
        user=settings.MYSQL_USER, password=settings.MYSQL_PASS, database=settings.MYSQL_DB,
        autocommit=True,
    )


@pytest.fixture
def cliente(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def headers():
    token = create_access_token(
        sub="1", uid=1, scopes=["facturacion:cargar", "facturacion:leer"], role="admin",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def datos_sinteticos():
    con = _conn()
    try:
        with con.cursor() as cur:
            for nro, es_org in ((NRO_CLINICA, 1), (NRO_CIRUJANO, 0), (NRO_AYUDANTE, 0)):
                cur.execute(
                    "INSERT INTO listado_medico (NRO_SOCIO, EXISTE, hashed_password, conceps_espec, es_organizacion) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (nro, "S", "x", '{"conceps": [], "espec": []}', es_org),
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
                (NRO_CLINICA, NRO_CIRUJANO, NRO_AYUDANTE),
            )
        con.close()


def _item(cod_medico, *, ejecutor=None, honorarios=0, ayudante=0) -> dict:
    return {
        "cod_medico": str(cod_medico), "cod_medico_ejecutor": str(ejecutor) if ejecutor else None,
        "dni_paciente": None, "fecha_practica": None, "cod_clinica": None, "autorizacion": None,
        "cod_nomenclador": "999901", "via": None, "cantidad": 1, "sesion": 1, "tipo_calculo": "M",
        "honorarios": honorarios, "gastos": 0, "ayudante": ayudante, "porcentaje": 100,
        "grupo_equipo_id": None,
    }


def _tipos(cliente, headers, cabeza_id):
    r = cliente.get(f"/api/facturacion/prestaciones/{cabeza_id}", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    return {p["cod_medico"]: p["tipo"] for p in [body] + body["grupo"]}


def test_ayudante_toma_el_tipo_de_la_cabeza_en_alta_y_edicion(cliente, headers, datos_sinteticos):
    payload = {
        "obra_social": COD_OBRA_TEST,
        "prestaciones": [
            # Cabeza: la clínica es el prestador (Sanatorio); cobra el cirujano ejecutor.
            _item(NRO_CLINICA, ejecutor=NRO_CIRUJANO, honorarios=100),
            _item(NRO_AYUDANTE, ayudante=25),
        ],
    }
    r = cliente.post("/api/facturacion/prestaciones", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    cabeza_id, ayudante_id = r.json()["ids"]

    # El ayudante NO se queda con el tipo del rango de código: lleva el de la cabeza.
    assert _tipos(cliente, headers, cabeza_id) == {
        str(NRO_CIRUJANO): "Sanatorio", str(NRO_AYUDANTE): "Sanatorio",
    }

    # Editar el precio del ayudante no le cambia el tipo.
    r2 = cliente.patch(f"/api/facturacion/prestaciones/{ayudante_id}", json={"ayudante": 30}, headers=headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["tipo"] == "Sanatorio"


def test_ayudante_sin_equipo_es_honorarios_individuales(cliente, headers, datos_sinteticos):
    r = cliente.post(
        "/api/facturacion/prestaciones", headers=headers,
        json={"obra_social": COD_OBRA_TEST, "prestaciones": [_item(NRO_AYUDANTE, ayudante=25)]},
    )
    assert r.status_code == 201, r.text
    (ayudante_id,) = r.json()["ids"]
    r2 = cliente.get(f"/api/facturacion/prestaciones/{ayudante_id}", headers=headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["tipo"] == "Honorarios individuales"
