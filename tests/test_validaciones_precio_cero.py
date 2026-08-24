"""Validaciones no carga en cero — nunca, con `CARGA_SIN_PRECIO` o sin él.

`settings.CARGA_SIN_PRECIO` es un permiso de **facturación**: deja pasar un
código sin valor vigente con los montos en 0 para que el operador del Colegio
pueda cargar la prestación y corregir el importe antes de cerrar el período.

En el panel del prestador no hay quien corrija: la fila se graba sola, entra a
la liquidación y el médico se entera cuando cobra de menos. Y en las obras
sociales que validan en línea es peor, porque la autorización ya consumió el
token del afiliado — la prestación quedó hecha en la obra social y facturada en
cero acá. Por eso `Contexto.precio()` rechaza el cero por su cuenta, sin mirar
el flag, y `buscar_codigos()` ni siquiera los ofrece.

Es lo que pasó con `420130`/`420302`/`420305` antes del import de precios del
2026-08-19: el buscador los mostraba a $ 0,00, Sancor los autorizaba y el
Colegio facturaba nada.
"""
import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.modules.facturacion.schemas import PrecioResponse
from app.modules.validaciones.core import contrato
from app.modules.validaciones.core.contrato import Contexto, factura_en_cero


def _precio(honorarios="0", gastos="0", *, admitido=True, motivo=None, presupuesto=False):
    return PrecioResponse(
        honorarios=Decimal(honorarios),
        gastos=Decimal(gastos),
        ayudante=Decimal("0"),
        descripcion="",
        fuente="nm_historial_precio_codigo",
        admitido=admitido,
        motivo=motivo,
        por_presupuesto=presupuesto,
    )


def _ctx() -> Contexto:
    return Contexto(
        db=None, medico=None, obra_social=411, periodo="202608",
        fecha=datetime.date(2026, 8, 19), usuario_carga=1,
    )


# ── El predicado ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "honorarios, gastos, en_cero",
    [
        ("0", "0", True),
        ("29158.59", "0", False),
        ("0", "4207.10", False),        # sólo gastos ya es facturable
        ("0.00", "0.00", True),
    ],
)
def test_factura_en_cero_mira_honorarios_mas_gastos(honorarios, gastos, en_cero):
    """El ayudante no cuenta: una validación es siempre una prestación simple
    del propio médico, sin equipo (ver `core/grabado.py`)."""
    assert factura_en_cero(_precio(honorarios, gastos)) is en_cero


def test_por_presupuesto_cuenta_como_cero():
    """`por_presupuesto` trae H/G/A en 0 y el monto lo carga a mano un operador
    desde facturación. El panel del prestador no tiene dónde cargarlo, así que
    por esta vía la prestación se facturaría en 0."""
    assert factura_en_cero(_precio(presupuesto=True)) is True


# ── El bloqueo en el alta ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_precio_cero_se_rechaza_aunque_el_lookup_lo_admita(monkeypatch):
    """El caso exacto de `CARGA_SIN_PRECIO=true`: `resolver_precio` devuelve
    `admitido=True` con los montos en 0. Validaciones lo corta igual."""

    async def _resolver(*_a, **_k):
        return _precio(motivo="La obra social no tiene un valor vigente para este código a esa fecha")

    monkeypatch.setattr(contrato, "resolver_precio", _resolver)

    with pytest.raises(HTTPException) as exc:
        await _ctx().precio("020130")

    assert exc.value.status_code == 422
    assert "020130" in exc.value.detail
    # El motivo del lookup viaja adelante: es lo que explica por qué ese código.
    assert "no tiene un valor vigente" in exc.value.detail


@pytest.mark.asyncio
async def test_precio_con_valor_pasa(monkeypatch):
    async def _resolver(*_a, **_k):
        return _precio("29158.59", "0")

    monkeypatch.setattr(contrato, "resolver_precio", _resolver)

    p = await _ctx().precio("420130")
    assert p.honorarios == Decimal("29158.59")


@pytest.mark.asyncio
async def test_no_admitido_sigue_ganando_el_motivo_del_lookup(monkeypatch):
    """Un código no habilitado por especialidad tiene que seguir diciendo eso,
    no "no tiene valor vigente": son dos problemas distintos y el médico hace
    algo distinto con cada uno."""

    async def _resolver(*_a, **_k):
        return _precio(admitido=False, motivo="Este código no corresponde a las especialidades del médico")

    monkeypatch.setattr(contrato, "resolver_precio", _resolver)

    with pytest.raises(HTTPException) as exc:
        await _ctx().precio("420130")

    assert exc.value.status_code == 422
    assert exc.value.detail == "Este código no corresponde a las especialidades del médico"


@pytest.mark.asyncio
async def test_exigir_admitido_false_tolera_el_cero(monkeypatch):
    """La excepción a la regla: la gestión presencial de Sancor (`070660`) sólo
    deja constancia, no factura. Ahí el cero es el resultado esperado y perder
    el caso por un 422 sería peor. Ver `ValidadorSancor.validar()`."""

    async def _resolver(*_a, **_k):
        return _precio(admitido=False, motivo="sin valor vigente")

    monkeypatch.setattr(contrato, "resolver_precio", _resolver)

    p = await _ctx().precio("070660", exigir_admitido=False)
    assert p.honorarios == Decimal("0")
