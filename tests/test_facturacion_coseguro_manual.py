"""El coseguro respeta el modo de la prestación (automático / manual).

* Manual: el coseguro es lo que cargó el operador. Ni editar otros montos de la
  fila, ni cambiar su cantidad o porcentaje, ni pasarla a manual lo pisan con el
  sugerido del nomenclador.
* El recálculo de factura (`recotizar.py`) no toca las filas manuales, coseguro
  incluido; las automáticas sí toman el del precio vigente.

Datos 100% sintéticos (misma técnica que `test_facturacion_pediatra.py`): obra
social y socio ficticios, código inexistente y `tipo_calculo="M"`, así que no
depende de qué precios haya en la base de desarrollo.
"""
import datetime
from decimal import Decimal

import pymysql
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_access_token

COD_OBRA_TEST = "29997"  # no existe en el catálogo real de obras sociales
NRO_MEDICO = 999970001
PERIODO_SEED = "202001"
COD_SINTETICO = "999870"


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
def medico_sintetico():
    """Un médico ficticio + una cabecera cerrada vieja (bootstrapea el período
    automático). Limpia todo lo que haya quedado bajo `COD_OBRA_TEST` al final."""
    con = _conn()
    try:
        with con.cursor() as cur:
            cur.execute(
                "INSERT INTO listado_medico (NRO_SOCIO, estado_cuenta, hashed_password, conceps_espec) "
                "VALUES (%s, %s, %s, %s)",
                (NRO_MEDICO, "activo", "x", '{"conceps": [], "espec": []}'),
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
            cur.execute("DELETE FROM listado_medico WHERE NRO_SOCIO = %s", (NRO_MEDICO,))
        con.close()


def _item(*, honorarios, coseguro) -> dict:
    return {
        "cod_medico": str(NRO_MEDICO),
        "cod_medico_ejecutor": None,
        "dni_paciente": None,
        "fecha_practica": None,
        "cod_clinica": None,
        "autorizacion": None,
        "cod_nomenclador": COD_SINTETICO,
        "via": None,
        "cantidad": 1,
        "sesion": 1,
        "tipo_calculo": "M",
        "honorarios": honorarios,
        "gastos": 0,
        "ayudante": 0,
        "coseguro": coseguro,
        "porcentaje": 100,
    }


def _alta_manual(cliente, headers, *, honorarios=100, coseguro=20) -> int:
    r = cliente.post(
        "/api/facturacion/prestaciones",
        json={"obra_social": COD_OBRA_TEST, "prestaciones": [_item(honorarios=honorarios, coseguro=coseguro)]},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["ids"][0]


def _patch(cliente, headers, prestacion_id, cuerpo):
    r = cliente.patch(f"/api/facturacion/prestaciones/{prestacion_id}", json=cuerpo, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_alta_manual_guarda_el_coseguro_que_cargo_el_operador(cliente, headers, medico_sintetico):
    pid = _alta_manual(cliente, headers, honorarios=100, coseguro=20)
    fila = cliente.get(f"/api/facturacion/prestaciones/{pid}", headers=headers).json()
    assert fila["tipo_calculo"] == "M"
    assert Decimal(str(fila["coseguro"])) == Decimal("20")
    assert Decimal(str(fila["importe_total"])) == Decimal("80")


def test_editar_montos_de_una_manual_no_pisa_el_coseguro(cliente, headers, medico_sintetico):
    pid = _alta_manual(cliente, headers, honorarios=100, coseguro=20)

    # Cambian honorarios y porcentaje (disparan la recotización del PATCH) sin mandar el coseguro.
    r = _patch(cliente, headers, pid, {"honorarios": 150, "porcentaje": 100})
    assert Decimal(str(r["coseguro"])) == Decimal("20")
    assert Decimal(str(r["importe_total"])) == Decimal("130")

    # Cantidad y sesión: el total escala, el coseguro sigue siendo el cargado.
    r = _patch(cliente, headers, pid, {"cantidad": 2})
    assert Decimal(str(r["coseguro"])) == Decimal("20")
    assert Decimal(str(r["importe_total"])) == Decimal("260")

    # Reafirmar el modo manual tampoco lo toca.
    r = _patch(cliente, headers, pid, {"tipo_calculo": "M"})
    assert Decimal(str(r["coseguro"])) == Decimal("20")


def test_coseguro_manual_se_puede_cambiar_a_mano(cliente, headers, medico_sintetico):
    pid = _alta_manual(cliente, headers, honorarios=100, coseguro=20)
    r = _patch(cliente, headers, pid, {"coseguro": 35})
    assert Decimal(str(r["coseguro"])) == Decimal("35")
    assert Decimal(str(r["importe_total"])) == Decimal("65")
