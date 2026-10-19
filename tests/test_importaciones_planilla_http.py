"""La importación por planilla, por HTTP: permisos, lote, cancelación y estado.

Corre contra la base de desarrollo. Lo único que escribe de verdad es el test que confirma
una fila en la obra social de prueba 888, período 209912, y la borra en un `finally`.
Los tests con TestClient se corren de a uno (`-k`): en Windows, varios en el mismo
archivo comparten un event loop que el primero deja cerrado.
"""
import uuid

import pymysql
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_access_token
from app.modules.importaciones.planilla import estado

OS, PERIODO = 888, "209912"
BASE = "/api/importaciones/planilla"


@pytest.fixture
def cliente(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _headers(*scopes):
    t = create_access_token(sub="1", uid=1, scopes=list(scopes), role="admin")
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture
def headers():
    return _headers("facturacion:cargar")


def _conn():
    return pymysql.connect(
        host=settings.MYSQL_HOST, port=settings.MYSQL_PORT or 3306, user=settings.MYSQL_USER,
        password=settings.MYSQL_PASS, database=settings.MYSQL_DB, autocommit=True,
    )


def _medico():
    con = _conn()
    try:
        with con.cursor() as cur:
            cur.execute(
                "SELECT NRO_SOCIO, MATRICULA_PROV FROM listado_medico "
                "WHERE EXISTE='S' AND es_organizacion=0 AND MATRICULA_PROV>0 ORDER BY NRO_SOCIO LIMIT 1"
            )
            return cur.fetchone()
    finally:
        con.close()


def _cuerpo(lote=None):
    nro, mat = _medico()
    fila = dict(fila=2, tipo="CONSULTA", nro_socio=str(nro), matricula=str(mat), codigo="420101", cantidad="1",
                calculo="M", honorarios="24000", fecha="2026-09-08", autorizacion="HTTP-1", afiliado="PACIENTE HTTP")
    cuerpo = dict(obra_social=OS, periodo=PERIODO, archivo="t.xlsx", filas=[fila])
    if lote:
        cuerpo["lote"] = lote
    return cuerpo


def _filas_en_prueba():
    con = _conn()
    try:
        with con.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM detalle_facturacion WHERE cod_obr='888' AND periodo=%s", (PERIODO,))
            return cur.fetchone()[0]
    finally:
        con.close()


def _limpiar():
    con = _conn()
    try:
        with con.cursor() as cur:
            cur.execute("DELETE FROM detalle_facturacion WHERE cod_obr='888' AND periodo=%s", (PERIODO,))
            cur.execute("DELETE FROM facturacion WHERE cod_obr='888' AND periodo=%s", (PERIODO,))
    finally:
        con.close()


def test_sin_el_permiso_de_carga_no_se_puede_importar(cliente):
    h = _headers("facturacion:leer")
    assert cliente.get(f"{BASE}/plantilla", headers=h).status_code == 403
    assert cliente.post(f"{BASE}/previsualizar", json=_cuerpo(), headers=h).status_code == 403
    assert cliente.post(f"{BASE}/lotes/{uuid.uuid4().hex}/cancelar", headers=h).status_code == 403


def test_la_plantilla_se_descarga_como_xlsx(cliente, headers):
    r = cliente.get(f"{BASE}/plantilla", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument")
    assert "plantilla_importacion_prestaciones.xlsx" in r.headers["content-disposition"]
    assert r.content[:2] == b"PK"  # un .xlsx es un zip


def test_confirmar_exige_el_lote(cliente, headers):
    r = cliente.post(f"{BASE}/confirmar", json=_cuerpo(), headers=headers)
    assert r.status_code == 422 and "lote" in r.text.lower()


def test_un_lote_invalido_se_rechaza(cliente, headers):
    assert cliente.post(f"{BASE}/lotes/..%2Fx/cancelar", headers=headers).status_code in (404, 422)
    assert cliente.post(f"{BASE}/lotes/no-es-hex/cancelar", headers=headers).status_code == 422
    r = cliente.get(f"{BASE}/lotes/zzzz", params={"obra_social": OS, "periodo": PERIODO}, headers=headers)
    assert r.status_code == 422
    r = cliente.post(f"{BASE}/confirmar", json=_cuerpo("no-es-hex"), headers=headers)
    assert r.status_code == 422


def test_cancelar_antes_de_empezar_responde_409_y_no_graba(cliente, headers):
    lote = uuid.uuid4().hex
    antes = _filas_en_prueba()
    try:
        r = cliente.post(f"{BASE}/lotes/{lote}/cancelar", headers=headers)
        assert r.status_code == 202 and r.json()["pedida"] is True

        r = cliente.post(f"{BASE}/confirmar", json=_cuerpo(lote), headers=headers)
        assert r.status_code == 409 and "cancelada" in r.text
        assert _filas_en_prueba() == antes

        e = cliente.get(f"{BASE}/lotes/{lote}", params={"obra_social": OS, "periodo": PERIODO}, headers=headers).json()
        assert e["estado"] == "cancelada" and e["grabadas"] == 0
    finally:
        _limpiar()


def test_confirmar_graba_y_el_estado_del_lote_lo_cuenta(cliente, headers):
    lote = uuid.uuid4().hex
    try:
        r = cliente.post(f"{BASE}/confirmar", json=_cuerpo(lote), headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["resumen"]["grabables"] == 1 and r.json()["filas"][0]["resultado"] == "grabada"

        e = cliente.get(f"{BASE}/lotes/{lote}", params={"obra_social": OS, "periodo": PERIODO}, headers=headers).json()
        assert e["estado"] == "grabada" and e["grabadas"] == 1 and float(e["importe_total"]) == 24000.0
        # El usuario que importó queda en la fila (nro_socio del token), no vacío.
        con = _conn()
        with con.cursor() as cur:
            cur.execute("SELECT usuario FROM detalle_facturacion WHERE cod_obr='888' AND periodo=%s", (PERIODO,))
            assert cur.fetchone()[0] == "1"
        con.close()
    finally:
        _limpiar()


def test_un_error_de_validacion_deja_el_estado_en_error(cliente, headers):
    lote = uuid.uuid4().hex
    cuerpo = _cuerpo(lote)
    cuerpo["periodo"] = "209912"
    cuerpo["obra_social"] = 99999  # no existe
    try:
        r = cliente.post(f"{BASE}/confirmar", json=cuerpo, headers=headers)
        assert r.status_code == 422
        e = cliente.get(f"{BASE}/lotes/{lote}", params={"obra_social": 99999, "periodo": PERIODO}, headers=headers).json()
        assert e["estado"] == "error" and e["grabadas"] == 0
    finally:
        _limpiar()


def test_demasiadas_filas_se_rechazan(cliente, headers):
    cuerpo = _cuerpo()
    cuerpo["filas"] = cuerpo["filas"] * 5001
    r = cliente.post(f"{BASE}/previsualizar", json=cuerpo, headers=headers)
    assert r.status_code in (413, 422)


# ── Estado en disco ──────────────────────────────────────────────────────────


@pytest.fixture
def dir_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(estado, "DIRECTORIO", tmp_path / "cmc_planilla")
    return tmp_path / "cmc_planilla"


def test_el_estado_se_escribe_y_se_lee(dir_tmp):
    lote = uuid.uuid4().hex
    assert estado.leer(lote) is None
    estado.escribir(lote, "validando", 3, 10, forzar=True)
    assert estado.leer(lote)["estado"] == "validando" and estado.leer(lote)["hecho"] == 3
    estado.escribir(lote, "grabada", 10, 10, forzar=True)
    assert estado.leer(lote)["estado"] == "grabada" and estado.leer(lote)["estado"] in estado.FINALES


def test_las_escrituras_de_avance_se_espacian_pero_los_cambios_de_estado_no(dir_tmp):
    lote = uuid.uuid4().hex
    estado.escribir(lote, "validando", 1, 10, forzar=True)
    estado.escribir(lote, "validando", 2, 10)  # demasiado pronto: se omite
    assert estado.leer(lote)["hecho"] == 1
    estado.escribir(lote, "grabando", 0, 10, forzar=True)
    assert estado.leer(lote)["estado"] == "grabando"


def test_la_cancelacion_se_ve_entre_procesos_por_el_disco(dir_tmp):
    lote = uuid.uuid4().hex
    assert estado.cancelacion_pedida(lote) is False
    estado.pedir_cancelacion(lote)
    assert estado.cancelacion_pedida(lote) is True
    assert estado.cancelacion_pedida(uuid.uuid4().hex) is False


def test_un_lote_con_ruta_no_sirve_de_nombre_de_archivo(dir_tmp):
    for malo in ("../x", "a/b", "", "ñ" * 10, "x" * 41, "..\\..\\x"):
        assert estado.lote_valido(malo) is False
        with pytest.raises(ValueError):
            estado.pedir_cancelacion(malo)


def test_los_archivos_viejos_se_limpian(dir_tmp):
    import os
    import time

    viejo, nuevo = uuid.uuid4().hex, uuid.uuid4().hex
    estado.escribir(viejo, "grabada", forzar=True)
    estado.escribir(nuevo, "grabada", forzar=True)
    f = dir_tmp / f"{viejo}.estado"
    hace_dos_horas = time.time() - 7200
    os.utime(f, (hace_dos_horas, hace_dos_horas))
    estado.limpiar_viejos()
    assert estado.leer(viejo) is None and estado.leer(nuevo) is not None
