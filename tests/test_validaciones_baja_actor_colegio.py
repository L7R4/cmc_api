"""El Colegio, operando en nombre de un médico (`nro_socio` + scope
`medicos:leer`), tiene que poder dar de baja una prestación aunque la fase
médico del período ya esté cerrada — solo el propio médico queda bloqueado
por esa fase, igual que ya pasa en `facturacion._gate_edicion`.

Bug reportado 2026-09-29: `pipeline.eliminar_prestacion` gateaba SIEMPRE con
el criterio del médico (`_gate_carga(actor=ORIGEN_MEDICO)`), sin importar
quién pedía la baja. Datos 100% sintéticos, mismo patrón que
`test_facturacion_mi_recepcion.py`.
"""
import datetime

import pymysql
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.modules.validaciones.core import pipeline
from app.modules.validaciones.core.periodos import ORIGEN_COLEGIO, ORIGEN_MEDICO, actor_para

COD_OBRA_TEST = "29997"  # no existe en el catálogo real de obras sociales
PERIODO_TEST = "203001"  # sintético, no colisiona con datos reales
NRO_MEDICO = 999970001


def _conn():
    return pymysql.connect(
        host=settings.MYSQL_HOST, port=settings.MYSQL_PORT or 3306,
        user=settings.MYSQL_USER, password=settings.MYSQL_PASS, database=settings.MYSQL_DB,
        autocommit=True,
    )


@pytest.fixture
def cabecera_medico_cerrado():
    """Fase Colegio abierta ('A') y fase médico ya cerrada ('C') — el caso
    exacto del bug."""
    con = _conn()
    with con.cursor() as cur:
        cur.execute(
            "INSERT INTO facturacion "
            "(id_cliente, tipo_factura, nro_factura, tipo_factura_2, nro_factura_2, "
            " tipo_factura_3, nro_factura_3, periodo, cod_obr, fecha, fecha_envio, "
            " fecha_recep, importe, afip, usuario, estado, estado_doctor, version) "
            "VALUES (0, '', '', '', '', '', '', %s, %s, %s, %s, %s, 0, 'N', '1', 'A', 'C', 1)",
            (PERIODO_TEST, COD_OBRA_TEST, datetime.date(2030, 1, 1),
             datetime.date(2030, 1, 1), datetime.date(2030, 1, 1)),
        )
    try:
        yield
    finally:
        with con.cursor() as cur:
            cur.execute("DELETE FROM detalle_facturacion WHERE cod_obr = %s", (COD_OBRA_TEST,))
            cur.execute("DELETE FROM facturacion WHERE cod_obr = %s", (COD_OBRA_TEST,))
        con.close()


def _insertar_prestacion() -> int:
    con = _conn()
    try:
        with con.cursor() as cur:
            cur.execute(
                "INSERT INTO detalle_facturacion "
                "(periodo, cod_med, nro_orden, cod_obr, cod_nom, tpo_funcion, sesion, "
                " cantidad, porc, honorarios, gastos, ayudante, importe_total, manual, "
                " estado, usuario, origen_carga, validacion_estado, validacion_anulada, version) "
                "VALUES (%s, %s, 0, %s, %s, 'HO', 1, 1, 100, 100, 0, 0, 100, 'A', "
                " 'A', %s, 'medico', 'autorizada', 0, 1)",
                (PERIODO_TEST, str(NRO_MEDICO), COD_OBRA_TEST, "999870", str(NRO_MEDICO)),
            )
            return cur.lastrowid
    finally:
        con.close()


@pytest.mark.asyncio
async def test_medico_no_puede_eliminar_con_su_fase_cerrada(db, cabecera_medico_cerrado):
    prestacion_id = _insertar_prestacion()
    with pytest.raises(HTTPException) as exc:
        await pipeline.eliminar_prestacion(db, prestacion_id, NRO_MEDICO, ORIGEN_MEDICO)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_colegio_puede_eliminar_aunque_la_fase_medico_este_cerrada(
    db, cabecera_medico_cerrado
):
    prestacion_id = _insertar_prestacion()
    await pipeline.eliminar_prestacion(db, prestacion_id, NRO_MEDICO, ORIGEN_COLEGIO)

    con = _conn()
    try:
        with con.cursor() as cur:
            cur.execute(
                "SELECT validacion_anulada, estado FROM detalle_facturacion "
                "WHERE id_detalle_prestaciones = %s",
                (prestacion_id,),
            )
            anulada, estado = cur.fetchone()
    finally:
        con.close()
    assert anulada == 1
    assert estado == "X"


def test_actor_para_distingue_medico_de_colegio():
    assert actor_para({"nro_socio": "123"}, 123) == ORIGEN_MEDICO
    assert actor_para({"nro_socio": "123"}, 456) == ORIGEN_COLEGIO
    assert actor_para({"nro_socio": None}, 456) == ORIGEN_COLEGIO
