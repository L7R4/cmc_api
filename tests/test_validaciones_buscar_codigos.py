"""Buscador de códigos del panel del prestador (`validaciones/core/consultas.py`).

Parte de lo que el médico tiene habilitado en la obra social y busca ahí por código o
por descripción. Antes recorría el catálogo en orden de código, leía los primeros 100 y
descartaba lo no habilitado: sin texto mostraba sólo lo que caía entre los primeros
códigos, y "escleroterapia" no encontraba el 070660 (sólo tipeando el código).
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.modules.facturacion.schemas import PrecioResponse
from app.modules.validaciones.core import consultas

HABILITADOS = [
    {"codigo": "030801", "descripcion": "PAROTIDECTOMIA TOTAL"},
    {"codigo": "070660", "descripcion": "ESCLEROTERAPIA"},
    {"codigo": "070715", "descripcion": "TRATAMIENTO ESCLEROSANTE DE VARICES"},
    {"codigo": "420351", "descripcion": "CONSULTA ESPECIALIZADA"},
    {"codigo": "999999", "descripcion": "SIN PRECIO EN LA OS"},
]


class _DbFalsa:
    """`buscar_codigos` solo le pide los códigos con precio activo en la OS."""

    def __init__(self, con_precio):
        self._con_precio = con_precio

    async def execute(self, _stmt):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: list(self._con_precio)))


@pytest.fixture
def buscar(monkeypatch):
    async def _medico(_db, _nro):
        return SimpleNamespace(ID=1)

    async def _habilitados(_db, _medico, q, os_nro):
        assert q is None and os_nro == 411  # el filtro lo hace el buscador, no la consulta
        return HABILITADOS

    async def _precio(_db, _os, _medico, codigo, _hoy):
        monto = Decimal("0") if codigo == "030801" else Decimal("1000")
        return PrecioResponse(
            honorarios=monto, gastos=Decimal("0"), ayudante=Decimal("0"),
            descripcion=f"desc {codigo}", fuente="nm", admitido=True,
        )

    monkeypatch.setattr(consultas, "get_medico", _medico)
    monkeypatch.setattr(consultas.service_nm, "listar_codigos_habilitados", _habilitados)
    monkeypatch.setattr(consultas, "resolver_precio", _precio)

    async def _buscar(q, limite=20):
        db = _DbFalsa({"030801", "070660", "070715", "420351"})
        return [r["codigo"] for r in await consultas.buscar_codigos(db, 411, 55, q, limite)]

    return _buscar


async def test_sin_texto_trae_los_habilitados_con_precio(buscar):
    # 030801 cotiza en cero y 999999 no tiene precio en la OS: no se ofrecen.
    assert await buscar("") == ["070660", "070715", "420351"]


async def test_busca_por_descripcion_sin_acentos_ni_mayusculas(buscar):
    assert await buscar("escleroterapia") == ["070660"]
    assert await buscar("ESCLEROTERAPÍA") == ["070660"]
    assert await buscar("esclero") == ["070660", "070715"]


async def test_busca_por_codigo_y_por_varias_palabras(buscar):
    assert await buscar("070660") == ["070660"]
    assert await buscar("0706") == ["070660"]
    assert await buscar("consulta especializada") == ["420351"]


async def test_respeta_el_limite(buscar):
    assert await buscar("", limite=2) == ["070660", "070715"]


def test_el_codigo_va_antes_que_la_descripcion():
    habilitados = [
        {"codigo": "111111", "descripcion": "TRATAMIENTO 0706"},
        {"codigo": "070660", "descripcion": "ESCLEROTERAPIA"},
    ]
    assert [h["codigo"] for h in consultas._filtrar_habilitados(habilitados, "0706")] == ["070660", "111111"]
