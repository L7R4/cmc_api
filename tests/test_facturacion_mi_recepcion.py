""""Mi recepción" del médico — filtro `publicado` en `GET /prestaciones`,
`descripcion` resuelta en batch, y `GET /periodos-propios`.

Corre contra la base de desarrollo (ver `conftest.py`), con datos 100%
sintéticos — mismo patrón que `test_facturacion_publicado.py`.
"""
import datetime

import pymysql
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_access_token

COD_OBRA_TEST = "29996"  # no existe en el catálogo real de obras sociales
NRO_MEDICO = 999960001
NRO_MEDICO_2 = 999960002  # para el aislamiento de /periodos-propios
COD_NOMENCLADOR_TEST = "999860"  # sintético, tipo_calculo="M" no depende de precio real


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


def _token(*, sub: str, scopes: list[str], role: str = "admin"):
    return create_access_token(sub=sub, uid=int(sub), scopes=scopes, role=role)


@pytest.fixture
def headers_admin():
    """Personal del Colegio: puede cargar y ver todo. `medico:leer` es lo que hace
    que `filtro_socio()` NO fuerce el filtro al propio token — sin él, GET
    /prestaciones se limitaría a las prestaciones de este mismo usuario admin."""
    token = _token(
        sub="1",
        scopes=["facturacion:cargar", "facturacion:leer", "facturacion:periodo", "medico:leer"],
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def headers_medico():
    """El propio médico de la fixture: sólo lectura propia."""
    token = _token(
        sub=str(NRO_MEDICO), scopes=["facturacion:leer_propio"], role="medico",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def headers_medico_2():
    """Otro médico: sólo lectura propia, para el test de aislamiento."""
    token = _token(
        sub=str(NRO_MEDICO_2), scopes=["facturacion:leer_propio"], role="medico",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def medicos_sinteticos():
    con = _conn()
    try:
        with con.cursor() as cur:
            for nro in (NRO_MEDICO, NRO_MEDICO_2):
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
                ("202001", COD_OBRA_TEST, datetime.date(2020, 1, 1),
                 datetime.date(2020, 1, 1), datetime.date(2020, 1, 1)),
            )
        yield
    finally:
        with con.cursor() as cur:
            cur.execute("DELETE FROM detalle_facturacion WHERE cod_obr = %s", (COD_OBRA_TEST,))
            cur.execute("DELETE FROM facturacion WHERE cod_obr = %s", (COD_OBRA_TEST,))
            cur.execute(
                "DELETE FROM listado_medico WHERE NRO_SOCIO IN (%s, %s)",
                (NRO_MEDICO, NRO_MEDICO_2),
            )
        con.close()


def _cargar_prestacion(cliente, headers, *, nro_medico=NRO_MEDICO) -> tuple[int, str]:
    r = cliente.post(
        "/api/facturacion/prestaciones",
        json={"obra_social": COD_OBRA_TEST, "prestaciones": [{
            "cod_medico": str(nro_medico),
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


def _publicar(cliente, headers_admin, periodo: str, publicado: bool):
    r = cliente.patch(
        "/api/facturacion/facturas/publicado",
        json={"cod_obra": COD_OBRA_TEST, "periodo": periodo, "publicado": publicado},
        headers=headers_admin,
    )
    assert r.status_code == 200, r.text


# ── Filtro `publicado` en GET /prestaciones ─────────────────────────────────

def test_filtro_publicado_trae_solo_lo_publicado(cliente, headers_admin, medicos_sinteticos):
    id_publicada, periodo = _cargar_prestacion(cliente, headers_admin)
    id_no_publicada, _ = _cargar_prestacion(cliente, headers_admin)

    con = _conn()
    try:
        with con.cursor() as cur:
            cur.execute(
                "UPDATE detalle_facturacion SET publicado = 1 WHERE id_detalle_prestaciones = %s",
                (id_publicada,),
            )
    finally:
        con.close()

    r = cliente.get(
        "/api/facturacion/prestaciones",
        params={"cod_obra": COD_OBRA_TEST, "periodo": periodo, "publicado": True},
        headers=headers_admin,
    )
    assert r.status_code == 200, r.text
    ids = [p["id"] for p in r.json()]
    assert ids == [id_publicada]
    assert id_no_publicada not in ids


def test_sin_filtro_publicado_trae_todo(cliente, headers_admin, medicos_sinteticos):
    id1, periodo = _cargar_prestacion(cliente, headers_admin)
    id2, _ = _cargar_prestacion(cliente, headers_admin)

    r = cliente.get(
        "/api/facturacion/prestaciones",
        params={"cod_obra": COD_OBRA_TEST, "periodo": periodo},
        headers=headers_admin,
    )
    assert r.status_code == 200, r.text
    ids = {p["id"] for p in r.json()}
    assert {id1, id2} <= ids


# ── `descripcion` resuelta en batch ─────────────────────────────────────────

def test_descripcion_se_resuelve_cuando_hay_nomenclador_id(cliente, headers_admin, medicos_sinteticos):
    prestacion_id, _ = _cargar_prestacion(cliente, headers_admin)

    con = _conn()
    try:
        with con.cursor() as cur:
            cur.execute(
                "INSERT INTO nm_nomenclador (codigo, descripcion) VALUES (%s, %s)",
                (COD_NOMENCLADOR_TEST, "Consulta sintética de prueba"),
            )
            nomenclador_id = cur.lastrowid
            cur.execute(
                "UPDATE detalle_facturacion SET nomenclador_id = %s WHERE id_detalle_prestaciones = %s",
                (nomenclador_id, prestacion_id),
            )
        r = cliente.get(
            "/api/facturacion/prestaciones", params={"id": prestacion_id}, headers=headers_admin,
        )
        assert r.status_code == 200, r.text
        assert r.json()[0]["descripcion"] == "Consulta sintética de prueba"
    finally:
        with con.cursor() as cur:
            cur.execute("DELETE FROM nm_nomenclador WHERE codigo = %s", (COD_NOMENCLADOR_TEST,))
        con.close()


def test_descripcion_es_none_sin_nomenclador_id(cliente, headers_admin, medicos_sinteticos):
    prestacion_id, _ = _cargar_prestacion(cliente, headers_admin)  # tipo_calculo=M, sin nomenclador_id
    r = cliente.get(
        "/api/facturacion/prestaciones", params={"id": prestacion_id}, headers=headers_admin,
    )
    assert r.status_code == 200, r.text
    assert r.json()[0]["descripcion"] is None


# ── GET /periodos-propios ───────────────────────────────────────────────────

def test_periodos_propios_solo_del_medico_y_publicados(
    cliente, headers_admin, headers_medico, medicos_sinteticos,
):
    _, periodo = _cargar_prestacion(cliente, headers_admin, nro_medico=NRO_MEDICO)
    _cargar_prestacion(cliente, headers_admin, nro_medico=NRO_MEDICO_2)  # otro médico, no debe verse

    r0 = cliente.get("/api/facturacion/periodos-propios", headers=headers_medico)
    assert r0.status_code == 200, r0.text
    assert r0.json() == []  # todavía nada publicado

    _publicar(cliente, headers_admin, periodo, True)

    r1 = cliente.get("/api/facturacion/periodos-propios", headers=headers_medico)
    assert r1.status_code == 200, r1.text
    periodos = [p["periodo"] for p in r1.json()]
    assert periodos == [periodo]


def test_periodos_propios_no_ve_los_de_otro_medico(
    cliente, headers_admin, headers_medico, headers_medico_2, medicos_sinteticos,
):
    _, periodo_medico_2 = _cargar_prestacion(cliente, headers_admin, nro_medico=NRO_MEDICO_2)
    _publicar(cliente, headers_admin, periodo_medico_2, True)

    r = cliente.get("/api/facturacion/periodos-propios", headers=headers_medico)
    assert r.status_code == 200, r.text
    assert r.json() == []  # lo publicado es de NRO_MEDICO_2, no de NRO_MEDICO

    r2 = cliente.get("/api/facturacion/periodos-propios", headers=headers_medico_2)
    assert r2.status_code == 200, r2.text
    assert [p["periodo"] for p in r2.json()] == [periodo_medico_2]


# ── Ownership: un médico nunca ve prestaciones de otro por /prestaciones ────

def test_medico_pidiendo_cod_medico_de_otro_da_403(
    cliente, headers_admin, headers_medico, medicos_sinteticos,
):
    """`filtro_socio()` no narrowea en silencio: un médico sin `medico:leer` que
    pide explícitamente el `cod_medico` de otro recibe 403 (`ownership.py`)."""
    _cargar_prestacion(cliente, headers_admin, nro_medico=NRO_MEDICO_2)

    r = cliente.get(
        "/api/facturacion/prestaciones",
        params={"cod_medico": str(NRO_MEDICO_2)},
        headers=headers_medico,
    )
    assert r.status_code == 403, r.text


def test_medico_sin_cod_medico_ve_solo_lo_propio(
    cliente, headers_admin, headers_medico, medicos_sinteticos,
):
    id_propia, periodo = _cargar_prestacion(cliente, headers_admin, nro_medico=NRO_MEDICO)
    id_ajena, _ = _cargar_prestacion(cliente, headers_admin, nro_medico=NRO_MEDICO_2)

    r = cliente.get(
        "/api/facturacion/prestaciones",
        params={"cod_obra": COD_OBRA_TEST, "periodo": periodo},
        headers=headers_medico,
    )
    assert r.status_code == 200, r.text
    ids = {p["id"] for p in r.json()}
    assert id_propia in ids
    assert id_ajena not in ids
