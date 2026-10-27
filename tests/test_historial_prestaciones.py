"""Historial de prestaciones (`audit_log_facturacion`) y quién ve las eliminadas.

* Eliminar desde facturación exige motivo y deja un evento por fila, también las que
  arrastra el equipo. La ficha arma la franja de estado con ese evento.
* Alta, edición y auditoría dejan su evento; un PATCH que no cambia nada, no.
* Panel de validaciones del médico: las rechazadas por la O.S. sí, las eliminadas no.
* Mi recepción: ni eliminadas ni rechazadas.

Corre contra la base de desarrollo con datos 100% sintéticos (fixtures de
`test_facturacion_pediatra.py`, mismo patrón que `test_facturacion_anular_equipo.py`).
Los eventos de las filas sintéticas los borra `conftest._limpiar_historial_huerfano`.
"""
import json
from types import SimpleNamespace

import pymysql
import pytest

from app.core.config import settings
from app.modules.facturacion import service
from app.modules.validaciones.core import consultas
from tests.test_facturacion_pediatra import (  # noqa: F401  (fixtures)
    COD_CESAREA, COD_OBRA_TEST, COD_PEDIATRA, NRO_AYUDANTE, NRO_CIRUJANO, NRO_PEDIATRA,
    _conn, _item, _post, cliente, headers, medicos_sinteticos,
)


def _eventos(ids, tipo=None):
    con = _conn()
    try:
        with con.cursor(pymysql.cursors.DictCursor) as cur:
            sql = (
                "SELECT * FROM audit_log_facturacion WHERE prestacion_id IN ("
                + ",".join(["%s"] * len(ids)) + ")"
            )
            params = list(ids)
            if tipo:
                sql += " AND tipo = %s"
                params.append(tipo)
            cur.execute(sql + " ORDER BY id", params)
            filas = cur.fetchall()
    finally:
        con.close()
    for f in filas:
        for c in ("cambios", "contexto"):
            if isinstance(f[c], str):
                f[c] = json.loads(f[c])
    return filas


def _equipo(cliente, headers):
    r = _post(cliente, headers, [
        _item(NRO_CIRUJANO, COD_CESAREA, honorarios=100),
        _item(NRO_AYUDANTE, COD_CESAREA, ayudante=10),
    ])
    assert r.status_code == 201, r.text
    cabeza_id, ayudante_id = r.json()["ids"]
    r2 = _post(cliente, headers, [
        _item(NRO_PEDIATRA, COD_PEDIATRA, honorarios=50, rol="pediatra", grupo_equipo_id=cabeza_id),
    ])
    assert r2.status_code == 201, r2.text
    return cabeza_id, ayudante_id, r2.json()["ids"][0]


# ── Eliminar con motivo ──────────────────────────────────────────────────────

def test_eliminar_sin_motivo_o_con_otro_sin_detalle_no_hace_nada(cliente, headers, medicos_sinteticos):
    r = _post(cliente, headers, [_item(NRO_CIRUJANO, COD_CESAREA, honorarios=100)])
    pid = r.json()["ids"][0]
    url = f"/api/facturacion/prestaciones/{pid}"

    assert cliente.delete(url, headers=headers).status_code == 422
    assert cliente.delete(f"{url}?motivo=porque_si", headers=headers).status_code == 422
    assert cliente.delete(f"{url}?motivo=otro", headers=headers).status_code == 422
    assert cliente.delete(f"{url}?motivo=otro&detalle=%20%20", headers=headers).status_code == 422

    assert not _eventos([pid], "eliminada")
    assert cliente.get(f"{url}/ficha", headers=headers).json()["prestacion"]["estado"] == "A"


def test_eliminar_con_equipo_deja_un_evento_por_fila_con_el_motivo(cliente, headers, medicos_sinteticos):
    cabeza, ayudante, pediatra = _equipo(cliente, headers)
    r = cliente.delete(
        f"/api/facturacion/prestaciones/{cabeza}?con_equipo=true&motivo=otro&detalle=Cargada%20dos%20veces",
        headers=headers,
    )
    assert r.status_code == 204, r.text

    eventos = {e["prestacion_id"]: e for e in _eventos([cabeza, ayudante, pediatra], "eliminada")}
    assert set(eventos) == {cabeza, ayudante, pediatra}
    for e in eventos.values():
        assert (e["estado_antes"], e["estado_despues"]) == ("A", "X")
        assert (e["motivo_codigo"], e["motivo_texto"]) == ("otro", "Cargada dos veces")
        assert (e["actor_tipo"], e["actor_socio"]) == ("colegio", 1)
        assert e["request_id"]
    assert "arrastrada_por" not in eventos[cabeza]["contexto"]
    assert eventos[ayudante]["contexto"]["arrastrada_por"] == cabeza
    assert eventos[pediatra]["contexto"]["arrastrada_por"] == cabeza

    ficha = cliente.get(f"/api/facturacion/prestaciones/{ayudante}/ficha", headers=headers).json()
    assert ficha["estado"]["clase"] == "eliminada_colegio"
    assert ficha["estado"]["motivo_codigo"] == "otro"
    assert ficha["estado"]["motivo_texto"] == "Cargada dos veces"
    assert ficha["historial"][0]["tipo"] == "eliminada"
    assert ficha["historial"][-1]["tipo"] == "cargada"


def test_quitar_integrante_al_editar_el_equipo_usa_el_motivo_interno(cliente, headers, medicos_sinteticos):
    _, ayudante, _ = _equipo(cliente, headers)
    r = cliente.delete(f"/api/facturacion/prestaciones/{ayudante}?motivo=equipo_editado", headers=headers)
    assert r.status_code == 204, r.text
    (e,) = _eventos([ayudante], "eliminada")
    assert e["motivo_codigo"] == "equipo_editado"


# ── Alta, edición y auditoría ────────────────────────────────────────────────

def test_alta_y_edicion_guardan_antes_y_despues(cliente, headers, medicos_sinteticos):
    r = _post(cliente, headers, [_item(NRO_CIRUJANO, COD_CESAREA, honorarios=100)])
    pid = r.json()["ids"][0]

    (alta,) = _eventos([pid], "cargada")
    assert alta["estado_despues"] == "A"
    assert alta["contexto"]["cod_obr"] == COD_OBRA_TEST
    assert alta["contexto"]["importe_total"] == "100.00"

    r = cliente.patch(f"/api/facturacion/prestaciones/{pid}", json={"honorarios": 150}, headers=headers)
    assert r.status_code == 200, r.text
    (edicion,) = _eventos([pid], "editada")
    assert edicion["cambios"]["honorarios"] == ["100.00", "150.00"]
    assert edicion["cambios"]["importe_total"] == ["100.00", "150.00"]

    # El front reenvía el formulario entero: si nada cambió, no hay evento.
    r = cliente.patch(f"/api/facturacion/prestaciones/{pid}", json={"honorarios": 150}, headers=headers)
    assert r.status_code == 200, r.text
    ediciones = _eventos([pid], "editada")
    assert len(ediciones) == 1, [e["cambios"] for e in ediciones]


def test_auditar_y_desauditar_solo_registra_lo_que_cambia(cliente, headers, medicos_sinteticos):
    r = _post(cliente, headers, [_item(NRO_CIRUJANO, COD_CESAREA, honorarios=100)])
    pid = r.json()["ids"][0]
    url = "/api/facturacion/prestaciones/revisado"

    assert cliente.patch(url, json={"marcados": [pid], "desmarcados": []}, headers=headers).status_code == 200
    assert cliente.patch(url, json={"marcados": [pid], "desmarcados": []}, headers=headers).status_code == 200
    assert cliente.patch(url, json={"marcados": [], "desmarcados": [pid]}, headers=headers).status_code == 200

    tipos = [e["tipo"] for e in _eventos([pid]) if e["tipo"] in ("auditada", "desauditada")]
    assert tipos == ["auditada", "desauditada"]


# ── Franja de la ficha sin historial (filas anteriores) ──────────────────────

def _fila(**kw):
    base = dict(estado="X", validacion_estado=None, validacion_detalle=None, validacion_anulada=False)
    return SimpleNamespace(**{**base, **kw})


@pytest.mark.parametrize("fila, clase", [
    (_fila(estado="A"), "vigente"),
    (_fila(estado="C"), "cerrada"),
    (_fila(estado="L"), "liquidada"),
    (_fila(validacion_estado="rechazada", validacion_detalle="Sin cobertura"), "rechazada_os"),
    (_fila(validacion_estado="pendiente"), "rechazada_os"),
    (_fila(validacion_estado="rechazada", validacion_anulada=True), "eliminada_medico"),
    (_fila(validacion_estado="autorizada"), "eliminada"),
    (_fila(), "eliminada"),
])
def test_estado_de_ficha_sin_eventos(fila, clase):
    assert service.estado_de_ficha(fila, []).clase == clase


# ── Visibilidad para el médico ───────────────────────────────────────────────

COD_OBRA_VIS = "29996"   # no existe en el catálogo real de obras sociales
NRO_MEDICO_VIS = 999960001
PERIODO_VIS = "203002"   # sintético


@pytest.fixture
def filas_visibilidad():
    """Una de cada: vigente, rechazada por la O.S., eliminada por el Colegio y dada de
    baja por el médico. Todas publicadas, para probar también la recepción."""
    casos = {
        "vigente": ("A", "autorizada", 0),
        "rechazada": ("X", "rechazada", 0),
        "eliminada_colegio": ("X", "autorizada", 0),
        "baja_medico": ("X", "autorizada", 1),
    }
    con = _conn()
    ids = {}
    try:
        with con.cursor() as cur:
            for nombre, (estado, val_estado, anulada) in casos.items():
                cur.execute(
                    "INSERT INTO detalle_facturacion (periodo, cod_med, cod_obr, cod_nom, nro_orden, "
                    " tpo_funcion, sesion, cantidad, porc, manual, "
                    " honorarios, gastos, ayudante, importe_total, estado, usuario, origen_carga, "
                    " validacion_estado, validacion_anulada, publicado, version, coseguro) "
                    "VALUES (%s, %s, %s, '420101', '0', 'H', 1, 1, 100, 'A', "
                    " 10, 0, 0, 10, %s, %s, 'medico', %s, %s, 1, 1, 0)",
                    (PERIODO_VIS, str(NRO_MEDICO_VIS), COD_OBRA_VIS, estado, str(NRO_MEDICO_VIS),
                     val_estado, anulada),
                )
                ids[nombre] = cur.lastrowid
        yield ids
    finally:
        with con.cursor() as cur:
            cur.execute("DELETE FROM detalle_facturacion WHERE cod_obr = %s", (COD_OBRA_VIS,))
        con.close()


@pytest.mark.asyncio
async def test_panel_de_validaciones_muestra_rechazadas_y_no_eliminadas(db, filas_visibilidad):
    salida = await consultas.listar_prestaciones(db, NRO_MEDICO_VIS, int(COD_OBRA_VIS), 2, 2030)
    assert {p["id"] for p in salida} == {filas_visibilidad["vigente"], filas_visibilidad["rechazada"]}

    (periodo,) = await consultas.listar_periodos(db, NRO_MEDICO_VIS, int(COD_OBRA_VIS))
    assert periodo["cantidad"] == 2


@pytest.mark.asyncio
async def test_mi_recepcion_solo_muestra_lo_vigente(db, filas_visibilidad):
    rows, total = await service.listar_prestaciones(
        db, cod_medico=str(NRO_MEDICO_VIS), periodo=PERIODO_VIS, publicado=True, limit=50, offset=0,
    )
    assert [r.id for r in rows] == [filas_visibilidad["vigente"]]
    assert total == 1

    periodos = await service.listar_periodos_propios(db, str(NRO_MEDICO_VIS))
    assert [p["periodo"] for p in periodos] == [PERIODO_VIS]
