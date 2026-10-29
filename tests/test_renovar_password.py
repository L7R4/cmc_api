""""Renovar contraseña" desde el legajo: contraseña provisoria aleatoria que vence.

Los primeros tests no tocan la base. Los de HTTP corren contra la base de
desarrollo (ver `conftest.py`) con dos socios 100% sintéticos (NRO_SOCIO fuera
del rango real) que se borran al terminar junto con sus sesiones y roles: un
operativo `facturador` y un socio `medico`.

Necesitan `scripts/renovar_password_2026-10-29.sql` aplicado en esa base.
"""
import re
from datetime import datetime, timedelta, timezone

import pymysql
import pytest
from fastapi.testclient import TestClient

from app.auth.deps import get_current_user, usuario_opcional
from app.core.config import settings
from app.core.passwords import (
    MENSAJE_PROVISORIA_VENCIDA,
    PASSWORD_INICIAL,
    generar_password_provisoria,
    hash_password,
    hash_password_inicial,
    password_provisoria_vencida,
    tiene_password_provisoria,
    verify_password,
)
from app.core.security import create_access_token, decode_token
from app.modules.medicos.service import permisos_que_faltan

NRO_OPERADOR = 999970101
NRO_SOCIO = 999970102
PASSWORD_OPERADOR = "Operador-Renovar-2026"
PASSWORD_SOCIO = "Socio-Renovar-2026"


def _ahora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Sin base ─────────────────────────────────────────────────────────────────

def test_la_provisoria_es_aleatoria_y_dictable():
    vistas = {generar_password_provisoria() for _ in range(200)}
    # Aleatoria: 200 sorteos sobre ~40 bits no se repiten.
    assert len(vistas) == 200
    for pwd in vistas:
        assert re.fullmatch(r"[a-z2-9]{4}-[a-z2-9]{4}", pwd), pwd
        # Nada que se confunda al dictarla o leerla.
        assert not set(pwd) & set("01oil"), pwd
        assert pwd != PASSWORD_INICIAL


def test_la_provisoria_verifica_contra_su_hash():
    pwd = generar_password_provisoria()
    assert verify_password(pwd, hash_password(pwd))


class _Cuenta:
    def __init__(self, vence):
        self.password_provisoria_vence = vence


def test_vencimiento_de_la_provisoria():
    # La del alta (cmc1785) no es provisoria: no vence.
    assert not tiene_password_provisoria(_Cuenta(None))
    assert not password_provisoria_vencida(_Cuenta(None))

    vigente = _Cuenta(_ahora() + timedelta(hours=1))
    assert tiene_password_provisoria(vigente)
    assert not password_provisoria_vencida(vigente)

    vencida = _Cuenta(_ahora() - timedelta(minutes=1))
    assert tiene_password_provisoria(vencida)
    assert password_provisoria_vencida(vencida)


MEDICO = [
    "medico:leer_propio", "facturacion:leer_propio", "liquidacion:leer_propio",
    "validacion:cargar", "catalogo:leer", "contenido:leer", "nomenclador:leer",
]
LIQUIDADOR = ["liquidacion:leer", "pago:leer", "pago:cerrar", "medico:leer", "medico:editar",
              "medico:renovar_password", "contenido:leer"]
FACTURADOR = ["facturacion:leer", "facturacion:cargar", "medico:leer", "medico:editar",
              "medico:renovar_password", "validacion:cargar", "catalogo:leer",
              "contenido:leer", "nomenclador:leer"]


def test_los_permisos_base_del_medico_no_cuentan():
    # El liquidador no carga validaciones ni lee el nomenclador, y aun así tiene
    # que poder renovarle la contraseña a un médico.
    assert permisos_que_faltan(LIQUIDADOR, MEDICO) == set()
    assert permisos_que_faltan(FACTURADOR, MEDICO) == set()


def test_no_se_renueva_una_cuenta_con_mas_permisos():
    admin = FACTURADOR + ["rbac:gestionar", "pago:reabrir"]
    assert permisos_que_faltan(FACTURADOR, admin) == {"rbac:gestionar", "pago:reabrir"}
    # Un liquidador a un facturador: tampoco, carga y cierra facturación.
    assert "facturacion:cargar" in permisos_que_faltan(LIQUIDADOR, FACTURADOR)


def test_cuenta_un_permiso_suelto_de_un_socio():
    # Un socio con rol `medico` y un permiso administrativo por `user_permission`.
    socio_con_extra = MEDICO + ["pago:reabrir"]
    assert permisos_que_faltan(FACTURADOR, socio_con_extra) == {"pago:reabrir"}


def test_los_codigos_obsoletos_no_cuentan():
    assert permisos_que_faltan(FACTURADOR, MEDICO + ["medicos:leer", "web:editor"]) == set()


def test_el_token_de_la_provisoria_va_marcado():
    marcado = create_access_token(sub="1", uid=1, scopes=["medico:leer_propio"], provisoria=True)
    normal = create_access_token(sub="1", uid=1, scopes=["medico:leer_propio"])

    assert decode_token(marcado)["pwd_prov"] is True
    assert "pwd_prov" not in decode_token(normal)

    class _Creds:
        def __init__(self, t):
            self.credentials = t

    assert get_current_user(_Creds(marcado))["pwd_prov"] is True
    assert get_current_user(_Creds(normal))["pwd_prov"] is False
    # En los endpoints públicos con respuesta reducida cuenta como visitante.
    assert usuario_opcional(_Creds(marcado)) is None
    assert usuario_opcional(_Creds(normal)) is not None


# ── Contra la base ───────────────────────────────────────────────────────────

def _conn():
    return pymysql.connect(
        host=settings.MYSQL_HOST, port=settings.MYSQL_PORT or 3306,
        user=settings.MYSQL_USER, password=settings.MYSQL_PASS, database=settings.MYSQL_DB,
        autocommit=True,
    )


def _fijar_roles(con, medico_id: int, roles: list[str]) -> None:
    with con.cursor() as cur:
        cur.execute("DELETE FROM user_role WHERE user_id = %s", (medico_id,))
        for nombre in roles:
            cur.execute(
                "INSERT INTO user_role (user_id, role_id) SELECT %s, id FROM roles WHERE name = %s",
                (medico_id, nombre),
            )


def _crear(con, nro: int, password_hash: str, roles: list[str]) -> int:
    with con.cursor() as cur:
        cur.execute(
            "INSERT INTO listado_medico (NRO_SOCIO, estado_cuenta, hashed_password, conceps_espec) "
            "VALUES (%s, 'activo', %s, %s)",
            (nro, password_hash, '{"conceps": [], "espec": []}'),
        )
        medico_id = cur.lastrowid
    _fijar_roles(con, medico_id, roles)
    return medico_id


@pytest.fixture(scope="module")
def _cliente_modulo(app):
    # Uno solo para todo el módulo: el pool de `app.db.database` queda atado al
    # event loop del primer TestClient, y abrir otro por test da 500 ("Event loop
    # is closed") a partir del segundo.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def cliente(_cliente_modulo):
    _cliente_modulo.cookies.clear()
    return _cliente_modulo


@pytest.fixture
def cuentas():
    """(con, id del operativo facturador, id del socio médico)."""
    con = _conn()
    ids = []
    try:
        op = _crear(con, NRO_OPERADOR, hash_password(PASSWORD_OPERADOR), ["facturador"])
        ids.append(op)
        socio = _crear(con, NRO_SOCIO, hash_password(PASSWORD_SOCIO), ["medico"])
        ids.append(socio)
        yield con, op, socio
    finally:
        with con.cursor() as cur:
            for i in ids:
                cur.execute("DELETE FROM user_role WHERE user_id = %s", (i,))
                cur.execute("DELETE FROM user_permission WHERE user_id = %s", (i,))
                cur.execute("DELETE FROM refresh_tokens WHERE user_id = %s", (i,))
                cur.execute("DELETE FROM listado_medico WHERE ID = %s", (i,))
        con.close()


def _token_operador(op_id: int) -> dict:
    # Los scopes del handler se releen de la base (rol facturador); los del token
    # sólo los usa la matriz de enforce_authz.
    t = create_access_token(
        sub=str(NRO_OPERADOR), uid=op_id,
        scopes=["medico:renovar_password", "medico:leer"], role="facturador",
    )
    return {"Authorization": f"Bearer {t}"}


def _renovar(cliente, op_id: int, medico_id: int):
    return cliente.post(f"/api/medicos/{medico_id}/renovar-password", headers=_token_operador(op_id))


def _login(cliente, password: str, nro: int = NRO_SOCIO):
    return cliente.post("/auth/login", json={"nro_socio": nro, "password": password})


def _fila(con, medico_id: int) -> dict:
    with con.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(
            "SELECT must_change_password, password_provisoria_vence FROM listado_medico WHERE ID = %s",
            (medico_id,),
        )
        return cur.fetchone()


def test_flujo_completo(cliente, cuentas):
    con, op, socio = cuentas

    # El socio tiene una sesión abierta: la renovación la tiene que cortar.
    assert _login(cliente, PASSWORD_SOCIO).status_code == 200
    csrf = cliente.cookies.get("csrf_token")

    r = _renovar(cliente, op, socio)
    assert r.status_code == 200, r.text
    assert r.headers.get("cache-control") == "no-store"
    cuerpo = r.json()
    provisoria = cuerpo["password_provisoria"]
    assert cuerpo["medico_id"] == socio
    assert cuerpo["horas_vigencia"] == 48

    fila = _fila(con, socio)
    assert fila["must_change_password"] == 1
    assert fila["password_provisoria_vence"] > _ahora() + timedelta(hours=47)

    # La sesión de antes murió.
    assert cliente.post("/auth/refresh", headers={"X-CSRF-Token": csrf}).status_code == 401
    # La contraseña vieja ya no entra.
    assert _login(cliente, PASSWORD_SOCIO).status_code == 401

    # Con la provisoria entra, pero sólo para cambiarla.
    r = _login(cliente, provisoria)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["must_change_password"] is True
    access = r.json()["access_token"]
    assert decode_token(access)["pwd_prov"] is True
    auth = {"Authorization": f"Bearer {access}"}

    r = cliente.get("/api/mobile/perfil", headers=auth)
    assert r.status_code == 403
    assert r.json()["detail"] == "password_provisoria"
    assert cliente.get("/auth/me", headers=auth).status_code == 200

    # El refresh conserva la marca.
    r = cliente.post("/auth/refresh", headers={"X-CSRF-Token": cliente.cookies.get("csrf_token")})
    assert r.status_code == 200, r.text
    assert decode_token(r.json()["access_token"])["pwd_prov"] is True

    r = cliente.post(
        "/auth/change-password", headers=auth,
        json={"old_password": provisoria, "new_password": "Nueva-Del-Socio-2026"},
    )
    assert r.status_code == 200, r.text

    fila = _fila(con, socio)
    assert fila["must_change_password"] == 0
    assert fila["password_provisoria_vence"] is None

    r = _login(cliente, "Nueva-Del-Socio-2026")
    assert r.status_code == 200
    assert "pwd_prov" not in decode_token(r.json()["access_token"])


def test_la_provisoria_vencida_no_entra(cliente, cuentas):
    con, op, socio = cuentas
    provisoria = _renovar(cliente, op, socio).json()["password_provisoria"]

    with con.cursor() as cur:
        cur.execute(
            "UPDATE listado_medico SET password_provisoria_vence = %s WHERE ID = %s",
            (_ahora() - timedelta(minutes=1), socio),
        )

    r = _login(cliente, provisoria)
    assert r.status_code == 401
    assert r.json()["detail"] == MENSAJE_PROVISORIA_VENCIDA

    r = cliente.post("/api/mobile/auth/login", json={"nro_socio": NRO_SOCIO, "password": provisoria})
    assert r.status_code == 401
    assert r.json()["detail"] == MENSAJE_PROVISORIA_VENCIDA


def test_el_app_movil_no_entra_con_la_provisoria(cliente, cuentas):
    _, op, socio = cuentas
    provisoria = _renovar(cliente, op, socio).json()["password_provisoria"]

    r = cliente.post("/api/mobile/auth/login", json={"nro_socio": NRO_SOCIO, "password": provisoria})
    assert r.status_code == 403
    assert "web" in r.json()["detail"]


def test_la_del_alta_no_es_provisoria(cliente, cuentas):
    """cmc1785 sigue igual: obliga a cambiarla, pero no vence ni bloquea la API."""
    con, _, socio = cuentas
    with con.cursor() as cur:
        cur.execute(
            "UPDATE listado_medico SET hashed_password = %s, must_change_password = 1 WHERE ID = %s",
            (hash_password_inicial(), socio),
        )

    r = _login(cliente, PASSWORD_INICIAL)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["must_change_password"] is True
    assert "pwd_prov" not in decode_token(r.json()["access_token"])
    assert _fila(con, socio)["password_provisoria_vence"] is None


def test_sin_el_permiso_no_se_renueva(cliente, cuentas):
    con, op, socio = cuentas
    _fijar_roles(con, op, ["medico"])
    assert _renovar(cliente, op, socio).status_code == 403
    assert _login(cliente, PASSWORD_SOCIO).status_code == 200


def test_no_se_renueva_la_de_un_admin(cliente, cuentas):
    con, op, socio = cuentas
    _fijar_roles(con, socio, ["admin"])

    r = _renovar(cliente, op, socio)
    assert r.status_code == 403
    assert "rbac:gestionar" in r.json()["detail"]
    # No se tocó nada.
    assert _fila(con, socio)["password_provisoria_vence"] is None


def test_no_se_renueva_la_propia(cliente, cuentas):
    _, op, _ = cuentas
    assert _renovar(cliente, op, op).status_code == 400
    assert _login(cliente, PASSWORD_OPERADOR, nro=NRO_OPERADOR).status_code == 200
