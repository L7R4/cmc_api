"""Quién inicia sesión: `estado_cuenta`, rol asignado y rol principal.

Corre contra la base de desarrollo (ver `conftest.py`) con un socio 100% sintético
(NRO_SOCIO fuera del rango real) que se borra al terminar, junto con sus sesiones
y sus roles. Nace activo y con rol `medico`.
"""
import pymysql
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.passwords import hash_password
from app.core.security import create_access_token

NRO_SOCIO = 999970001
PASSWORD = "Prueba-Estado-2026"


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
def socio():
    """Crea el socio sintético; devuelve una función para fijarle el estado y su ID."""
    con = _conn()
    with con.cursor() as cur:
        cur.execute(
            "INSERT INTO listado_medico (NRO_SOCIO, estado_cuenta, hashed_password, conceps_espec) "
            "VALUES (%s, 'activo', %s, %s)",
            (NRO_SOCIO, hash_password(PASSWORD), '{"conceps": [], "espec": []}'),
        )
        medico_id = cur.lastrowid
    _fijar_roles(con, medico_id, ["medico"])

    def fijar_estado(estado: str) -> None:
        with con.cursor() as cur:
            cur.execute(
                "UPDATE listado_medico SET estado_cuenta = %s WHERE ID = %s", (estado, medico_id)
            )

    try:
        yield medico_id, fijar_estado, lambda roles: _fijar_roles(con, medico_id, roles)
    finally:
        with con.cursor() as cur:
            cur.execute("DELETE FROM user_role WHERE user_id = %s", (medico_id,))
            cur.execute("DELETE FROM refresh_tokens WHERE user_id = %s", (medico_id,))
            cur.execute("DELETE FROM listado_medico WHERE ID = %s", (medico_id,))
        con.close()


def _fijar_roles(con, medico_id: int, roles: list[str]) -> None:
    with con.cursor() as cur:
        cur.execute("DELETE FROM user_role WHERE user_id = %s", (medico_id,))
        for nombre in roles:
            cur.execute(
                "INSERT INTO user_role (user_id, role_id) SELECT %s, id FROM roles WHERE name = %s",
                (medico_id, nombre),
            )


def _login(cliente):
    return cliente.post("/auth/login", json={"nro_socio": NRO_SOCIO, "password": PASSWORD})


def test_solo_la_cuenta_activa_inicia_sesion(cliente, socio):
    _, fijar_estado, _ = socio

    assert _login(cliente).status_code == 200

    fijar_estado("pendiente")
    r = _login(cliente)
    assert r.status_code == 403
    assert "pendiente" in r.json()["detail"]

    fijar_estado("baja")
    r = _login(cliente)
    assert r.status_code == 403
    assert "baja" in r.json()["detail"]


def test_sin_rol_no_inicia_sesion(cliente, socio):
    _, _, fijar_roles = socio
    fijar_roles([])
    r = _login(cliente)
    assert r.status_code == 403
    assert "rol" in r.json()["detail"]


def test_con_varios_roles_gana_el_de_mas_alcance(cliente, socio):
    _, _, fijar_roles = socio
    fijar_roles(["medico", "facturador", "liquidador"])
    r = _login(cliente)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["role"] == "liquidador"


def test_dar_de_baja_corta_la_sesion_abierta(cliente, socio):
    medico_id, _, _ = socio

    assert _login(cliente).status_code == 200
    csrf = cliente.cookies.get("csrf_token")

    admin = create_access_token(sub="1", uid=1, scopes=["medico:editar"], role="admin")
    r = cliente.patch(
        f"/api/medicos/{medico_id}/estado",
        json={"estado_cuenta": "baja"},
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["estado_cuenta"] == "baja"

    # El refresh que tenía abierto ya no sirve.
    r = cliente.post("/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 401


def test_estado_invalido_se_rechaza(cliente, socio):
    medico_id, _, _ = socio
    admin = create_access_token(sub="1", uid=1, scopes=["medico:editar"], role="admin")
    r = cliente.patch(
        f"/api/medicos/{medico_id}/estado",
        json={"estado_cuenta": "S"},
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 422
