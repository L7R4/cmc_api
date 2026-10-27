"""Actualizar padrones: alta y baja masiva de socios en el padrón de una obra social.

Corre contra la base de desarrollo (ver `conftest.py`) con socios 100% sintéticos
(NRO_SOCIO fuera del rango real) cuyas filas de `medico_obra_social` se borran al terminar.
"""
import pymysql
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.passwords import hash_password
from app.core.security import create_access_token

NROS = (999970101, 999970102)
MARCA_NOMBRE = "ZZPRUEBAPADRON"


def _conn():
    return pymysql.connect(
        host=settings.MYSQL_HOST, port=settings.MYSQL_PORT or 3306,
        user=settings.MYSQL_USER, password=settings.MYSQL_PASS, database=settings.MYSQL_DB,
        autocommit=True,
    )


def _headers(*scopes: str) -> dict:
    token = create_access_token(sub="1", uid=1, scopes=list(scopes), role="admin")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def cliente(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def os_y_planes():
    """Una obra social principal activa y los códigos de plan activos de su familia."""
    con = _conn()
    try:
        with con.cursor() as cur:
            cur.execute(
                "SELECT p.ID, p.NRO_OBRASOCIAL, COUNT(a.ID) AS n FROM obras_sociales p "
                "LEFT JOIN obras_sociales a ON a.obra_social_principal_id = p.ID AND a.activo = 1 "
                "WHERE p.activo = 1 AND p.obra_social_principal_id IS NULL "
                "GROUP BY p.ID, p.NRO_OBRASOCIAL ORDER BY n DESC, p.ID LIMIT 1"
            )
            pid, nro, _ = cur.fetchone()
            cur.execute(
                "SELECT NRO_OBRASOCIAL FROM obras_sociales WHERE activo = 1 "
                "AND (ID = %s OR obra_social_principal_id = %s)", (pid, pid),
            )
            planes = sorted(r[0] for r in cur.fetchall())
    finally:
        con.close()
    return nro, planes


@pytest.fixture
def socios():
    """Dos socios activos sintéticos; devuelve sus IDs y la conexión."""
    con = _conn()
    ids = []
    with con.cursor() as cur:
        for i, nro in enumerate(NROS):
            cur.execute(
                "INSERT INTO listado_medico (NRO_SOCIO, NOMBRE, estado_cuenta, hashed_password, conceps_espec) "
                "VALUES (%s, %s, 'activo', %s, %s)",
                (nro, f"{MARCA_NOMBRE} {i}", hash_password("x-Prueba-2026"), '{"conceps": [], "espec": []}'),
            )
            ids.append(cur.lastrowid)
    try:
        yield ids, con
    finally:
        with con.cursor() as cur:
            cur.execute("DELETE FROM medico_obra_social WHERE NRO_SOCIO IN %s", (NROS,))
            cur.execute("DELETE FROM listado_medico WHERE NRO_SOCIO IN %s", (NROS,))
        con.close()


def _marcas(con, planes):
    with con.cursor() as cur:
        cur.execute(
            "SELECT NRO_SOCIO, NRO_OBRASOCIAL, MARCA FROM medico_obra_social "
            "WHERE NRO_SOCIO IN %s AND NRO_OBRASOCIAL IN %s", (NROS, tuple(planes)),
        )
        return cur.fetchall()


def test_adherir_crea_en_todos_los_planes_y_es_idempotente(cliente, socios, os_y_planes):
    ids, con = socios
    nro_os, planes = os_y_planes
    h = _headers("padron:editar", "padron:leer")

    r = cliente.post(f"/api/padrones/obras-sociales/{nro_os}/adherir", json={"medico_ids": ids}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"procesados": 2, "sin_cambios": 0, "omitidos": []}

    filas = _marcas(con, planes)
    assert len(filas) == 2 * len(planes)
    assert {m for _, _, m in filas} == {"S"}

    r = cliente.post(f"/api/padrones/obras-sociales/{nro_os}/adherir", json={"medico_ids": ids}, headers=h)
    assert r.json() == {"procesados": 0, "sin_cambios": 2, "omitidos": []}
    assert len(_marcas(con, planes)) == 2 * len(planes)

    r = cliente.get(
        f"/api/padrones/obras-sociales/{nro_os}/candidatos",
        params={"search": MARCA_NOMBRE, "vista": "ad"}, headers=h,
    )
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["total"] == 2
    assert all(it["ADHERIDO"] for it in cuerpo["items"])


def test_adherir_reactiva_la_marca_n_y_omite_a_quien_no_esta_activo(cliente, socios, os_y_planes):
    ids, con = socios
    nro_os, planes = os_y_planes
    h = _headers("padron:editar", "padron:leer")
    cliente.post(f"/api/padrones/obras-sociales/{nro_os}/adherir", json={"medico_ids": ids}, headers=h)

    with con.cursor() as cur:
        cur.execute("UPDATE medico_obra_social SET MARCA = 'N' WHERE NRO_SOCIO = %s", (NROS[0],))
        cur.execute("UPDATE listado_medico SET estado_cuenta = 'baja' WHERE NRO_SOCIO = %s", (NROS[1],))

    r = cliente.post(
        f"/api/padrones/obras-sociales/{nro_os}/adherir",
        json={"medico_ids": [*ids, 2_000_000_000]}, headers=h,
    )
    assert r.status_code == 200, r.text
    # Reactiva al primero; el segundo está de baja y el último no existe.
    assert r.json() == {"procesados": 1, "sin_cambios": 0, "omitidos": [ids[1], 2_000_000_000]}
    assert {m for n, _, m in _marcas(con, planes) if n == NROS[0]} == {"S"}


def test_quitar_borra_la_familia_y_las_vistas_lo_reflejan(cliente, socios, os_y_planes):
    ids, con = socios
    nro_os, planes = os_y_planes
    h = _headers("padron:editar", "padron:leer")
    cliente.post(f"/api/padrones/obras-sociales/{nro_os}/adherir", json={"medico_ids": ids}, headers=h)

    r = cliente.post(f"/api/padrones/obras-sociales/{nro_os}/quitar", json={"medico_ids": ids[:1]}, headers=h)
    assert r.json() == {"procesados": 1, "sin_cambios": 0, "omitidos": []}
    assert {n for n, _, _ in _marcas(con, planes)} == {NROS[1]}

    r = cliente.post(f"/api/padrones/obras-sociales/{nro_os}/quitar", json={"medico_ids": ids[:1]}, headers=h)
    assert r.json() == {"procesados": 0, "sin_cambios": 1, "omitidos": []}

    r = cliente.get(
        f"/api/padrones/obras-sociales/{nro_os}/candidatos",
        params={"search": MARCA_NOMBRE, "vista": "sin"}, headers=h,
    )
    assert [it["NRO_SOCIO"] for it in r.json()["items"]] == [NROS[0]]


def test_sin_padron_editar_no_se_puede_modificar(cliente, socios, os_y_planes):
    ids, _ = socios
    nro_os, _ = os_y_planes
    h = _headers("padron:leer")
    assert cliente.post(f"/api/padrones/obras-sociales/{nro_os}/adherir", json={"medico_ids": ids}, headers=h).status_code == 403
    assert cliente.post(f"/api/padrones/obras-sociales/{nro_os}/quitar", json={"medico_ids": ids}, headers=h).status_code == 403
