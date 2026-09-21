"""PATCH /facturacion/facturas/publicado — publicar/despublicar un período entero.

Bulk update de `detalle_facturacion.publicado` por (cod_obr, periodo), sin
distinguir versión ni estado — es el único camino de la app para tocar esa
columna. El gating del lado del médico (qué ve "desde su recepción") queda
fuera de este cambio; acá sólo se verifica que el botón/endpoint administrativo
actualiza en bloque y que `GET /facturas` refleja el estado agregado
correctamente ("alcanza con una fila en true" en esa OS+período).

Corre contra la base de desarrollo (ver `conftest.py`), con datos 100%
sintéticos — mismo patrón que `test_facturacion_pediatra.py`.
"""
import datetime

import pymysql
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_access_token

COD_OBRA_TEST = "29997"  # no existe en el catálogo real de obras sociales
NRO_MEDICO = 999970001
COD_NOMENCLADOR_TEST = "999870"  # sintético, tipo_calculo="M" no depende de precio real
PERIODO_SEED = "202001"  # bootstrapea el período automático (cabecera cerrada vieja)
PERIODO_SIN_CARGA = "209912"  # nunca tiene filas — para el 404


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
        sub="1", uid=1,
        scopes=["facturacion:cargar", "facturacion:leer", "facturacion:periodo"],
        role="admin",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def headers_sin_scope_periodo():
    """Mismo usuario, sin `facturacion:periodo` — para el test de 403."""
    token = create_access_token(
        sub="1", uid=1, scopes=["facturacion:cargar", "facturacion:leer"], role="admin",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def medico_sintetico():
    con = _conn()
    try:
        with con.cursor() as cur:
            cur.execute(
                "INSERT INTO listado_medico (NRO_SOCIO, EXISTE, hashed_password, conceps_espec) "
                "VALUES (%s, %s, %s, %s)",
                (NRO_MEDICO, "S", "x", '{"conceps": [], "espec": []}'),
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


def _cargar_prestacion(cliente, headers) -> tuple[int, str]:
    """Carga una prestación sintética vía la API real. Devuelve (id, periodo)."""
    r = cliente.post(
        "/api/facturacion/prestaciones",
        json={"obra_social": COD_OBRA_TEST, "prestaciones": [{
            "cod_medico": str(NRO_MEDICO),
            "cod_medico_ejecutor": None,
            "dni_paciente": None,
            "fecha_practica": None,
            "cod_clinica": None,
            "autorizacion": None,
            "cod_nomenclador": COD_NOMENCLADOR_TEST,
            "via": None,
            "cantidad": 1,
            "sesion": 1,
            "tipo_calculo": "M",
            "honorarios": 100,
            "gastos": 0,
            "ayudante": 0,
            "porcentaje": 100,
            "grupo_equipo_id": None,
            "rol": None,
        }]},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["ids"][0], body["periodo"]


def _publicar(cliente, headers, periodo: str, publicado: bool):
    return cliente.patch(
        "/api/facturacion/facturas/publicado",
        json={"cod_obra": COD_OBRA_TEST, "periodo": periodo, "publicado": publicado},
        headers=headers,
    )


def _listado(cliente, headers, periodo: str):
    r = cliente.get(
        "/api/facturacion/facturas",
        params={"cod_obra": COD_OBRA_TEST, "periodo": periodo},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_publicar_marca_todas_las_filas_y_el_listado_lo_refleja(cliente, headers, medico_sintetico):
    _, periodo = _cargar_prestacion(cliente, headers)
    _, _ = _cargar_prestacion(cliente, headers)

    r = _publicar(cliente, headers, periodo, True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["publicado"] is True
    assert body["filas_actualizadas"] >= 2

    listado = _listado(cliente, headers, periodo)
    assert listado
    assert all(f["publicado"] is True for f in listado)


def test_despublicar_vuelve_a_false(cliente, headers, medico_sintetico):
    _, periodo = _cargar_prestacion(cliente, headers)
    assert _publicar(cliente, headers, periodo, True).status_code == 200

    r = _publicar(cliente, headers, periodo, False)
    assert r.status_code == 200, r.text
    assert r.json()["publicado"] is False

    listado = _listado(cliente, headers, periodo)
    assert all(f["publicado"] is False for f in listado)


def test_alcanza_una_sola_fila_en_true_para_que_el_periodo_lea_publicado(cliente, headers, medico_sintetico):
    """`calcular_publicado` agrupa por (cod_obr, periodo): si una sola fila quedó
    en true (p. ej. una prestación nueva sobre un complemento sin publicar
    todavía), el período entero ya tiene que leer publicado=True."""
    id1, periodo = _cargar_prestacion(cliente, headers)
    _, _ = _cargar_prestacion(cliente, headers)

    con = _conn()
    try:
        with con.cursor() as cur:
            cur.execute(
                "UPDATE detalle_facturacion SET publicado = 1 WHERE id_detalle_prestaciones = %s",
                (id1,),
            )
    finally:
        con.close()

    listado = _listado(cliente, headers, periodo)
    assert listado
    assert all(f["publicado"] is True for f in listado)


def test_publicar_periodo_sin_prestaciones_da_404(cliente, headers, medico_sintetico):
    r = _publicar(cliente, headers, PERIODO_SIN_CARGA, True)
    assert r.status_code == 404, r.text


def test_publicar_sin_scope_da_403(cliente, headers, headers_sin_scope_periodo, medico_sintetico):
    _, periodo = _cargar_prestacion(cliente, headers)
    r = _publicar(cliente, headers_sin_scope_periodo, periodo, True)
    assert r.status_code == 403, r.text
