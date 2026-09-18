"""Rol "Pediatra" en el equipo quirúrgico (parto/cesárea).

El pediatra factura su PROPIO código (distinto al del cirujano), cobra en
`honorarios`, coseguro siempre 0, y queda en el mismo `grupo_equipo_id` que el
cirujano SIN poder ser la cabeza. El discriminador es `tpo_funcion='P'`
(columna legacy, no se agregó ninguna columna nueva) — no expuesto tal cual en
`PrestacionRead`, así que estos tests lo verifican indirectamente a través del
badge `tipo_prestador` (`_derivar_tipo_prestador` sólo devuelve "Pediatra"
cuando `tpo_funcion == 'P'`).

Corre contra la base de desarrollo (ver `conftest.py`), con datos 100%
sintéticos — mismo patrón que `test_facturacion_equipo_autorizacion.py`.

Los códigos principales de parto/cesárea (110401, 110403) son REALES del
catálogo compartido: hace falta que sean justamente esos strings porque
`CODIGOS_CON_PEDIATRA` los valida por igualdad exacta. Todos los ítems usan
`tipo_calculo="M"` (manual) para no depender de que haya un precio vigente
real — la validación de rol/cabeza/coseguro es independiente del lookup de
precio.
"""
import datetime

import pymysql
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_access_token

COD_OBRA_TEST = "29998"  # no existe en el catálogo real de obras sociales
NRO_CIRUJANO = 999980001
NRO_AYUDANTE = 999980002
NRO_PEDIATRA = 999980003
NRO_PEDIATRA_2 = 999980004  # para el caso "ya hay un pediatra"
NRO_OTRO_CIRUJANO = 999980005  # cabecera de un equipo NO parto/cesárea
PERIODO_SEED = "202001"

COD_PARTO = "110401"        # real — está en CODIGOS_CON_PEDIATRA
COD_CESAREA = "110403"      # real — está en CODIGOS_CON_PEDIATRA
COD_NO_PARTO = "999880"     # sintético — NO está en CODIGOS_CON_PEDIATRA
COD_PEDIATRA = "999881"     # sintético — código propio del pediatra, no del cirujano

TODOS_LOS_MEDICOS = (
    NRO_CIRUJANO, NRO_AYUDANTE, NRO_PEDIATRA, NRO_PEDIATRA_2, NRO_OTRO_CIRUJANO,
)


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
def medicos_sinteticos():
    """Médicos ficticios + una cabecera cerrada vieja (bootstrapea el período
    automático). Borra todo lo que haya quedado bajo `COD_OBRA_TEST` y los médicos
    ficticios al final — mismo patrón que `test_facturacion_equipo_autorizacion.py`."""
    con = _conn()
    try:
        with con.cursor() as cur:
            for nro in TODOS_LOS_MEDICOS:
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
                "DELETE FROM listado_medico WHERE NRO_SOCIO IN "
                "(" + ",".join(["%s"] * len(TODOS_LOS_MEDICOS)) + ")",
                TODOS_LOS_MEDICOS,
            )
        con.close()


def _item(cod_medico: int, cod_nomenclador: str, *, honorarios=0, ayudante=0, rol=None,
          grupo_equipo_id=None) -> dict:
    return {
        "cod_medico": str(cod_medico),
        "cod_medico_ejecutor": None,
        "dni_paciente": None,
        "fecha_practica": None,
        "cod_clinica": None,
        "autorizacion": None,
        "cod_nomenclador": cod_nomenclador,
        "via": None,
        "cantidad": 1,
        "sesion": 1,
        "tipo_calculo": "M",
        "honorarios": honorarios,
        "gastos": 0,
        "ayudante": ayudante,
        "porcentaje": 100,
        "grupo_equipo_id": grupo_equipo_id,
        "rol": rol,
    }


def _post(cliente, headers, items):
    return cliente.post(
        "/api/facturacion/prestaciones",
        json={"obra_social": COD_OBRA_TEST, "prestaciones": items},
        headers=headers,
    )


# ── Alta: cirujano + ayudante + pediatra en un solo POST ────────────────────

def test_pediatra_comparte_grupo_y_cirujano_es_cabeza(cliente, headers, medicos_sinteticos):
    r = _post(cliente, headers, [
        _item(NRO_CIRUJANO, COD_CESAREA, honorarios=100),
        _item(NRO_AYUDANTE, COD_CESAREA, ayudante=10),
        _item(NRO_PEDIATRA, COD_PEDIATRA, honorarios=50, rol="pediatra"),
    ])
    assert r.status_code == 201, r.text
    ids = r.json()["ids"]
    assert len(ids) == 3
    cabeza_id = ids[0]  # el cirujano es el primer ítem

    r2 = cliente.get(f"/api/facturacion/prestaciones/{cabeza_id}", headers=headers)
    assert r2.status_code == 200, r2.text
    cabeza = r2.json()
    assert cabeza["grupo_equipo_id"] == cabeza_id
    assert cabeza["tipo_prestador"] == "Medico"

    grupo = cabeza["grupo"]
    assert len(grupo) == 2  # ayudante + pediatra, sin contar a la cabeza
    pediatra_entry = next(p for p in grupo if p["cod_medico"] == str(NRO_PEDIATRA))
    assert pediatra_entry["tipo_prestador"] == "Pediatra"
    assert pediatra_entry["grupo_equipo_id"] == cabeza_id
    assert pediatra_entry["cod_nomenclador"] == COD_PEDIATRA  # su PROPIO código

    # Fila completa del pediatra: coseguro siempre 0, aunque no se haya enviado.
    r3 = cliente.get(f"/api/facturacion/prestaciones/{pediatra_entry['id']}", headers=headers)
    assert r3.status_code == 200, r3.text
    pediatra_row = r3.json()
    assert pediatra_row["tipo_prestador"] == "Pediatra"
    assert float(pediatra_row["coseguro"] or 0) == 0.0
    assert float(pediatra_row["honorarios"]) == 50.0


def test_pediatra_primero_en_el_payload_da_422(cliente, headers, medicos_sinteticos):
    r = _post(cliente, headers, [
        _item(NRO_PEDIATRA, COD_PEDIATRA, honorarios=50, rol="pediatra"),
        _item(NRO_CIRUJANO, COD_CESAREA, honorarios=100),
    ])
    assert r.status_code == 422, r.text
    assert "cirujano" in r.text.lower()


def test_dos_pediatras_da_422(cliente, headers, medicos_sinteticos):
    r = _post(cliente, headers, [
        _item(NRO_CIRUJANO, COD_CESAREA, honorarios=100),
        _item(NRO_PEDIATRA, COD_PEDIATRA, honorarios=50, rol="pediatra"),
        _item(NRO_PEDIATRA_2, COD_PEDIATRA, honorarios=50, rol="pediatra"),
    ])
    assert r.status_code == 422, r.text
    assert "un pediatra" in r.text.lower()


def test_codigo_principal_no_admite_pediatra_da_422(cliente, headers, medicos_sinteticos):
    r = _post(cliente, headers, [
        _item(NRO_CIRUJANO, COD_NO_PARTO, honorarios=100),
        _item(NRO_PEDIATRA, COD_PEDIATRA, honorarios=50, rol="pediatra"),
    ])
    assert r.status_code == 422, r.text
    assert "parto" in r.text.lower() or "cesárea" in r.text.lower()


def test_mismo_medico_cirujano_y_pediatra_da_422(cliente, headers, medicos_sinteticos):
    r = _post(cliente, headers, [
        _item(NRO_CIRUJANO, COD_CESAREA, honorarios=100),
        _item(NRO_CIRUJANO, COD_PEDIATRA, honorarios=50, rol="pediatra"),
    ])
    assert r.status_code == 422, r.text
    assert "repetir el mismo prestador" in r.text.lower()


# ── Edición: no degradar el rol al recotizar (regresión del bug encontrado) ──

def test_patch_pediatra_no_degrada_rol_ni_reinyecta_coseguro(cliente, headers, medicos_sinteticos):
    r = _post(cliente, headers, [
        _item(NRO_CIRUJANO, COD_CESAREA, honorarios=100),
        _item(NRO_PEDIATRA, COD_PEDIATRA, honorarios=50, rol="pediatra"),
    ])
    assert r.status_code == 201, r.text
    ids = r.json()["ids"]
    pediatra_id = ids[1]

    # PATCH que NO toca ningún campo de precio (cantidad no está en pricing_keys).
    r1 = cliente.patch(
        f"/api/facturacion/prestaciones/{pediatra_id}", json={"cantidad": 1}, headers=headers,
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["tipo_prestador"] == "Pediatra"
    assert float(r1.json()["coseguro"] or 0) == 0.0

    # PATCH que SÍ dispara recotización (porcentaje está en pricing_keys) — antes de
    # este fix, esto degradaba tpo_funcion de 'P' a 'H' en silencio.
    r2 = cliente.patch(
        f"/api/facturacion/prestaciones/{pediatra_id}",
        json={"porcentaje": 100, "honorarios": 60},
        headers=headers,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["tipo_prestador"] == "Pediatra"
    assert float(r2.json()["coseguro"] or 0) == 0.0
    assert float(r2.json()["honorarios"]) == 60.0


def test_pediatra_no_puede_ser_cabeza_al_editar(cliente, headers, medicos_sinteticos):
    r = _post(cliente, headers, [
        _item(NRO_CIRUJANO, COD_CESAREA, honorarios=100),
        _item(NRO_PEDIATRA, COD_PEDIATRA, honorarios=50, rol="pediatra"),
    ])
    ids = r.json()["ids"]
    pediatra_id = ids[1]

    r2 = cliente.patch(
        f"/api/facturacion/prestaciones/{pediatra_id}",
        json={"grupo_equipo_id": pediatra_id},  # intenta ser cabeza de sí mismo
        headers=headers,
    )
    assert r2.status_code == 422, r2.text
    assert "cabeza" in r2.text.lower()


# ── Agregar un pediatra a un equipo YA guardado (POST de un solo ítem) ──────

def test_agregar_pediatra_a_equipo_existente(cliente, headers, medicos_sinteticos):
    r = _post(cliente, headers, [
        _item(NRO_CIRUJANO, COD_PARTO, honorarios=100),
        _item(NRO_AYUDANTE, COD_PARTO, ayudante=10),
    ])
    assert r.status_code == 201, r.text
    cabeza_id = r.json()["ids"][0]

    r2 = _post(cliente, headers, [
        _item(NRO_PEDIATRA, COD_PEDIATRA, honorarios=50, rol="pediatra", grupo_equipo_id=cabeza_id),
    ])
    assert r2.status_code == 201, r2.text
    pediatra_id = r2.json()["ids"][0]

    r3 = cliente.get(f"/api/facturacion/prestaciones/{cabeza_id}", headers=headers)
    grupo = r3.json()["grupo"]
    assert len(grupo) == 2
    pediatra_entry = next(p for p in grupo if p["id"] == pediatra_id)
    assert pediatra_entry["tipo_prestador"] == "Pediatra"

    # Un segundo pediatra sobre el mismo equipo → 422 (máximo 1).
    r4 = _post(cliente, headers, [
        _item(NRO_PEDIATRA_2, COD_PEDIATRA, honorarios=50, rol="pediatra", grupo_equipo_id=cabeza_id),
    ])
    assert r4.status_code == 422, r4.text
    assert "ya tiene un pediatra" in r4.text.lower()


def test_agregar_pediatra_a_equipo_no_parto_da_422(cliente, headers, medicos_sinteticos):
    r = _post(cliente, headers, [
        _item(NRO_OTRO_CIRUJANO, COD_NO_PARTO, honorarios=100),
    ])
    assert r.status_code == 201, r.text
    solo_id = r.json()["ids"][0]

    r2 = _post(cliente, headers, [
        _item(NRO_PEDIATRA, COD_PEDIATRA, honorarios=50, rol="pediatra", grupo_equipo_id=solo_id),
    ])
    assert r2.status_code == 422, r2.text
    assert "parto" in r2.text.lower() or "cesárea" in r2.text.lower()
