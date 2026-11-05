"""Datos de cobro del socio por la API: PUT /medicos/{id} y el informe de pago.

Corre contra la base de desarrollo (ver `conftest.py`) con socios 100% sintéticos (NRO_SOCIO fuera del
rango real, CBU armados con el algoritmo del BCRA) que se borran al terminar, junto con el pago de prueba.
Necesita las columnas `cbu_santander`, `cbu_otro_banco` y `medio_cobro` en la base.
"""
import pymysql
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_access_token

NRO_BASE = 999971000
P1 = (7, 1, 3, 9, 7, 1, 3)
P2 = (3, 9, 7, 1, 3, 9, 7, 1, 3, 9, 7, 1, 3)


def armar_cbu(entidad: str, cuenta: str = "1234567890123") -> str:
    b1 = entidad + "0001"
    d1 = (10 - sum(int(a) * p for a, p in zip(b1, P1)) % 10) % 10
    d2 = (10 - sum(int(a) * p for a, p in zip(cuenta, P2)) % 10) % 10
    return f"{b1}{d1}{cuenta}{d2}"


SANTANDER = armar_cbu("072")
GALICIA = armar_cbu("007", "9876543210987")
CUIT_OK = "20123456786"
CUIT_30 = "30715678906"

CON_PERMISO = ["medico:editar", "medico:editar_bancario", "pago:leer"]
SIN_PERMISO = ["medico:editar", "pago:leer"]


def _conn():
    return pymysql.connect(
        host=settings.MYSQL_HOST, port=settings.MYSQL_PORT or 3306,
        user=settings.MYSQL_USER, password=settings.MYSQL_PASS, database=settings.MYSQL_DB,
        autocommit=True,
    )


@pytest.fixture(scope="module")
def cliente(app):
    # Uno solo para todo el módulo: el pool de `app.db.database` queda atado al event loop que lo
    # creó, y un cliente por test deja conexiones de un loop ya cerrado (500 desde el 2.º test).
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _token(scopes):
    return {"Authorization": "Bearer " + create_access_token(sub="1", uid=1, scopes=scopes, role="admin")}


@pytest.fixture
def con():
    c = _conn()
    yield c
    c.close()


@pytest.fixture
def socio(con):
    """Un socio sin datos de cobro (cheque)."""
    with con.cursor() as cur:
        cur.execute(
            "INSERT INTO listado_medico (NRO_SOCIO, NOMBRE, CUIT, estado_cuenta, hashed_password, conceps_espec) "
            "VALUES (%s, 'PRUEBA COBRO', %s, 'activo', 'x', %s)",
            (NRO_BASE, CUIT_OK, '{"conceps": [], "espec": []}'),
        )
        medico_id = cur.lastrowid
    yield medico_id
    with con.cursor() as cur:
        cur.execute("DELETE FROM listado_medico WHERE ID = %s", (medico_id,))


def _cobro(con, medico_id):
    with con.cursor() as cur:
        cur.execute("SELECT cbu_santander, cbu_otro_banco, medio_cobro FROM listado_medico WHERE ID = %s", (medico_id,))
        return cur.fetchone()


def test_sin_permiso_bancario_no_se_puede_cambiar_el_cobro(cliente, con, socio):
    r = cliente.put(f"/api/medicos/{socio}", json={"cbu_santander": SANTANDER, "medio_cobro": "santander"}, headers=_token(SIN_PERMISO))
    assert r.status_code == 403, r.text
    assert _cobro(con, socio) == (None, None, "cheque")


def test_con_permiso_bancario_se_guarda(cliente, con, socio):
    r = cliente.put(f"/api/medicos/{socio}", json={"cbu_santander": SANTANDER, "medio_cobro": "santander"}, headers=_token(CON_PERMISO))
    assert r.status_code == 204, r.text
    assert _cobro(con, socio) == (SANTANDER, None, "santander")


def test_repetir_el_cobro_sin_cambios_no_pide_permiso_bancario(cliente, con, socio):
    cliente.put(f"/api/medicos/{socio}", json={"cbu_santander": SANTANDER, "medio_cobro": "santander"}, headers=_token(CON_PERMISO))
    # El legajo manda todo el formulario: quien edita otra cosa y no toca el cobro no puede recibir un 403.
    r = cliente.put(
        f"/api/medicos/{socio}",
        json={"cbu_santander": SANTANDER, "cbu_otro_banco": None, "medio_cobro": "santander", "localidad": "CORRIENTES"},
        headers=_token(SIN_PERMISO),
    )
    assert r.status_code == 204, r.text


def test_cbu_invalido_es_422_y_no_se_guarda(cliente, con, socio):
    mal = SANTANDER[:-1] + ("0" if SANTANDER[-1] != "0" else "1")
    for cuerpo in (
        {"cbu_santander": mal},                                        # dígito verificador
        {"cbu_santander": GALICIA},                                    # otro banco en el campo de Santander
        {"cbu_otro_banco": SANTANDER},                                 # Santander en el campo de otro banco
        {"medio_cobro": "otros_bancos"},                               # el medio exige su CBU
        {"cbu_otro_banco": "1234567890", "medio_cobro": "otros_bancos"},  # cuenta de 10 dígitos
    ):
        r = cliente.put(f"/api/medicos/{socio}", json=cuerpo, headers=_token(CON_PERMISO))
        assert r.status_code == 422, (cuerpo, r.text)
    assert _cobro(con, socio) == (None, None, "cheque")


def test_valor_viejo_que_no_es_cbu_se_conserva_con_cheque(cliente, con, socio):
    with con.cursor() as cur:
        cur.execute("UPDATE listado_medico SET cbu_otro_banco = '1234567890', medio_cobro = 'cheque' WHERE ID = %s", (socio,))
    r = cliente.put(f"/api/medicos/{socio}", json={"cbu_otro_banco": "1234567890", "medio_cobro": "cheque", "localidad": "X"}, headers=_token(CON_PERMISO))
    assert r.status_code == 204, r.text
    assert _cobro(con, socio) == (None, "1234567890", "cheque")


def test_cheque_sin_cbu_es_un_estado_valido(cliente, con, socio):
    # un socio nuevo ya es cheque sin CBU, y reenviarlo así no es un error ni pide permiso bancario
    r = cliente.put(f"/api/medicos/{socio}", json={"cbu_santander": None, "cbu_otro_banco": None, "medio_cobro": "cheque"}, headers=_token(SIN_PERMISO))
    assert r.status_code == 204, r.text
    # y se puede pasar de Santander a cheque borrando el CBU en el mismo guardado
    cliente.put(f"/api/medicos/{socio}", json={"cbu_santander": SANTANDER, "medio_cobro": "santander"}, headers=_token(CON_PERMISO))
    r = cliente.put(f"/api/medicos/{socio}", json={"cbu_santander": "", "medio_cobro": "cheque"}, headers=_token(CON_PERMISO))
    assert r.status_code == 204, r.text
    assert _cobro(con, socio) == (None, None, "cheque")
    d = cliente.get(f"/api/medicos/{socio}", headers=_token(["medico:leer"])).json()
    assert (d["cbu_santander"], d["cbu_otro_banco"], d["medio_cobro"]) == (None, None, "cheque")


def test_el_detalle_devuelve_los_tres_campos(cliente, con, socio):
    r = cliente.put(f"/api/medicos/{socio}", json={"cbu_otro_banco": GALICIA, "medio_cobro": "otros_bancos"}, headers=_token(CON_PERMISO))
    assert r.status_code == 204, r.text
    r = cliente.get(f"/api/medicos/{socio}", headers=_token(["medico:leer"]))
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["cbu_santander"], d["cbu_otro_banco"], d["medio_cobro"]) == (None, GALICIA, "otros_bancos")


# ── informe de pago ──────────────────────────────────────────────────────────

@pytest.fixture
def pago_de_prueba(con):
    """Un pago cerrado con 6 socios sintéticos que cubren cada archivo, más uno con neto 0."""
    casos = [
        # nro,  medio,         santander, otro,   cuit,    neto
        (1, "santander", SANTANDER, None, CUIT_OK, 1000.5),
        (2, "santander", SANTANDER, None, CUIT_30, 2000),
        (3, "otros_bancos", None, GALICIA, CUIT_OK, 3000),
        (4, "otros_bancos", None, GALICIA, CUIT_30, 4000),
        (5, "cheque", None, "1234567890", CUIT_OK, 5000),
        (6, "santander", None, None, CUIT_OK, 6000),        # elige Santander y no tiene CBU: observado
        (7, "santander", SANTANDER, None, CUIT_OK, 0),      # neto 0: no sale en ningún archivo
    ]
    ids = []
    with con.cursor() as cur:
        cur.execute("INSERT INTO pago (anio, mes, descripcion, estado) VALUES (2099, 12, 'PRUEBA COBRO', 'C')")
        pago_id = cur.lastrowid
        for n, medio, cs, co, cuit, neto in casos:
            cur.execute(
                "INSERT INTO listado_medico (NRO_SOCIO, NOMBRE, CUIT, estado_cuenta, hashed_password, conceps_espec, "
                "cbu_santander, cbu_otro_banco, medio_cobro) VALUES (%s, %s, %s, 'activo', 'x', %s, %s, %s, %s)",
                (NRO_BASE + n, f"PRUEBA {n}", cuit, '{"conceps": [], "espec": []}', cs, co, medio),
            )
            mid = cur.lastrowid
            ids.append(mid)
            cur.execute(
                "INSERT INTO pago_medico (pago_id, medico_id, bruto, debitos, creditos, reconocido, deducciones, neto_a_pagar, estado) "
                "VALUES (%s, %s, %s, 0, 0, %s, 0, %s, 'pendiente')", (pago_id, mid, neto, neto, neto),
            )
    yield pago_id
    with con.cursor() as cur:
        cur.execute("DELETE FROM pago_medico WHERE pago_id = %s", (pago_id,))
        cur.execute("DELETE FROM pago WHERE id = %s", (pago_id,))
        cur.execute("DELETE FROM listado_medico WHERE ID IN (%s)" % ",".join(["%s"] * len(ids)), ids)


def _informe(cliente, pago_id, tipo):
    return cliente.get(f"/api/pagos/{pago_id}/informe/{tipo}", headers=_token(["pago:leer"]))


def test_informe_cada_socio_sale_en_un_solo_archivo(cliente, pago_de_prueba):
    esperado = {
        "santander": {NRO_BASE + 1},
        "santander_30": {NRO_BASE + 2},
        "otros_bancos": {NRO_BASE + 3},
        "otros_bancos_30": {NRO_BASE + 4},
        "cheques": {NRO_BASE + 5},
        "observados": {NRO_BASE + 6},
    }
    vistos = set()
    for tipo, nros in esperado.items():
        r = _informe(cliente, pago_de_prueba, tipo)
        assert r.status_code == 200, (tipo, r.text)
        d = r.json()
        assert {m["nro_socio"] for m in d["medicos"]} == nros, tipo
        assert vistos.isdisjoint(nros)
        vistos |= nros
    # el de neto 0 no está en ninguno
    assert NRO_BASE + 7 not in vistos


def test_informe_trae_el_cbu_del_medio_elegido_y_el_motivo_de_los_observados(cliente, pago_de_prueba):
    d = _informe(cliente, pago_de_prueba, "otros_bancos").json()
    assert d["medicos"][0]["cbu"] == GALICIA and d["medicos"][0]["motivo"] is None
    obs = _informe(cliente, pago_de_prueba, "observados").json()["medicos"][0]
    assert obs["cbu"] is None and "Santander" in obs["motivo"]
    ch = _informe(cliente, pago_de_prueba, "cheques").json()["medicos"][0]
    assert ch["cbu"] is None                       # el cheque no lleva CBU, aunque tenga un valor viejo guardado


def test_informe_resumen_cuenta_todos_los_archivos(cliente, pago_de_prueba):
    resumen = _informe(cliente, pago_de_prueba, "santander").json()["resumen"]
    assert resumen["santander"] == {"cantidad": 1, "total": "1000.50"}
    assert {k: v["cantidad"] for k, v in resumen.items()} == {
        "santander": 1, "santander_30": 1, "otros_bancos": 1, "otros_bancos_30": 1, "cheques": 1, "observados": 1}


def test_informe_pago_abierto_es_409_y_tipo_viejo_es_422(cliente, con, pago_de_prueba):
    with con.cursor() as cur:
        cur.execute("UPDATE pago SET estado = 'A' WHERE id = %s", (pago_de_prueba,))
    assert _informe(cliente, pago_de_prueba, "santander").status_code == 409
    assert _informe(cliente, pago_de_prueba, "cuit_30").status_code == 422
