"""Mover de período y auditar: la cabecera arrastra a su equipo, un integrante va solo.

Datos 100% sintéticos (fixtures de `test_facturacion_pediatra.py`).
"""
import pytest

from app.core.security import create_access_token
from tests.test_facturacion_pediatra import (  # noqa: F401  (fixtures)
    COD_CESAREA, COD_PEDIATRA, NRO_AYUDANTE, NRO_CIRUJANO, NRO_PEDIATRA,
    _item, _post, cliente, medicos_sinteticos,
)

REVISADO = "/api/facturacion/prestaciones/revisado"
MOVER = "/api/facturacion/prestaciones/mover-periodo"


@pytest.fixture
def headers():
    # Como el fixture compartido, más el permiso de mover de período.
    token = create_access_token(
        sub="1", uid=1, scopes=["facturacion:cargar", "facturacion:leer", "facturacion:periodo"], role="admin",
    )
    return {"Authorization": f"Bearer {token}"}


def _equipo(cliente, headers):
    r = _post(cliente, headers, [
        _item(NRO_CIRUJANO, COD_CESAREA, honorarios=100),
        _item(NRO_AYUDANTE, COD_CESAREA, ayudante=10),
    ])
    assert r.status_code == 201, r.text
    cabeza, ayudante = r.json()["ids"]
    r2 = _post(cliente, headers, [
        _item(NRO_PEDIATRA, COD_PEDIATRA, honorarios=50, rol="pediatra", grupo_equipo_id=cabeza),
    ])
    assert r2.status_code == 201, r2.text
    return cabeza, ayudante, r2.json()["ids"][0]


def _ficha(cliente, headers, pid):
    return cliente.get(f"/api/facturacion/prestaciones/{pid}/ficha", headers=headers).json()["prestacion"]


def _revisados(cliente, headers, ids):
    return {i: bool(_ficha(cliente, headers, i)["revisado"]) for i in ids}


def _periodos(cliente, headers, ids):
    return {i: _ficha(cliente, headers, i)["periodo"] for i in ids}


def test_auditar_la_cabecera_audita_a_todo_el_equipo(cliente, headers, medicos_sinteticos):
    cabeza, ayudante, pediatra = _equipo(cliente, headers)
    ids = [cabeza, ayudante, pediatra]

    r = cliente.patch(REVISADO, json={"marcados": [cabeza], "desmarcados": []}, headers=headers)
    assert r.status_code == 200, r.text
    assert {f["id"] for f in r.json()} == set(ids)
    assert all(_revisados(cliente, headers, ids).values())

    r = cliente.patch(REVISADO, json={"marcados": [], "desmarcados": [cabeza]}, headers=headers)
    assert r.status_code == 200, r.text
    assert not any(_revisados(cliente, headers, ids).values())


def test_auditar_un_integrante_no_toca_al_resto(cliente, headers, medicos_sinteticos):
    cabeza, ayudante, pediatra = _equipo(cliente, headers)

    r = cliente.patch(REVISADO, json={"marcados": [ayudante], "desmarcados": []}, headers=headers)
    assert r.status_code == 200, r.text
    assert [f["id"] for f in r.json()] == [ayudante]
    assert _revisados(cliente, headers, [cabeza, ayudante, pediatra]) == {
        cabeza: False, ayudante: True, pediatra: False,
    }


def test_mover_la_cabecera_mueve_a_todo_el_equipo(cliente, headers, medicos_sinteticos):
    cabeza, ayudante, pediatra = _equipo(cliente, headers)
    ids = [cabeza, ayudante, pediatra]
    ficha = _ficha(cliente, headers, cabeza)

    r = cliente.post(MOVER, json={
        "cod_obra": ficha["cod_obr"], "periodo_origen": ficha["periodo"],
        "ids": [cabeza], "direccion": "siguiente",
    }, headers=headers)
    assert r.status_code == 200, r.text
    assert set(r.json()["ids_movidos"]) == set(ids)
    destino = r.json()["periodo_destino"]
    assert set(_periodos(cliente, headers, ids).values()) == {destino}


def test_mover_un_integrante_lo_mueve_solo(cliente, headers, medicos_sinteticos):
    cabeza, ayudante, pediatra = _equipo(cliente, headers)
    ficha = _ficha(cliente, headers, ayudante)

    r = cliente.post(MOVER, json={
        "cod_obra": ficha["cod_obr"], "periodo_origen": ficha["periodo"],
        "ids": [ayudante], "direccion": "siguiente",
    }, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["ids_movidos"] == [ayudante]
    periodos = _periodos(cliente, headers, [cabeza, ayudante, pediatra])
    assert periodos[ayudante] == r.json()["periodo_destino"]
    assert periodos[cabeza] == periodos[pediatra] == ficha["periodo"]


def test_mover_la_cabecera_con_un_integrante_auditado_no_mueve_nada(cliente, headers, medicos_sinteticos):
    cabeza, ayudante, pediatra = _equipo(cliente, headers)
    ficha = _ficha(cliente, headers, cabeza)
    assert cliente.patch(REVISADO, json={"marcados": [ayudante], "desmarcados": []}, headers=headers).status_code == 200

    r = cliente.post(MOVER, json={
        "cod_obra": ficha["cod_obr"], "periodo_origen": ficha["periodo"],
        "ids": [cabeza], "direccion": "siguiente",
    }, headers=headers)
    assert r.status_code == 409, r.text
    assert set(_periodos(cliente, headers, [cabeza, ayudante, pediatra]).values()) == {ficha["periodo"]}
