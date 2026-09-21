"""Tests de `ValidadorManual` — Boreal Salud (O.S. 285) y Omint (O.S. 243).

Las dos comparten la misma implementación (`obras/manual.py::ValidadorManual`)
y difieren sólo en tres banderas de convenio (`descuenta_coseguro`,
`requiere_autorizacion`, `requiere_nombre`) más `admite_orden` (informativo,
no se usa en el flujo — ver el comentario en `manual.py`). Se testean las
instancias reales `BOREAL`/`OMINT` (mismas que usa el pipeline), no dobles.

`_validar_autorizacion_medico` (de `facturacion.service`, importada dentro de
`obras/manual.py`) toca la base cuando el código exige autorización y no vino
ninguna — se monkeypatchea para que estos tests no necesiten DB.
"""
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.modules.facturacion.schemas import PrecioResponse
from app.modules.validaciones.core.contrato import CERO, ValidadorOS
from app.modules.validaciones.obras import manual as manual_module
from app.modules.validaciones.obras.boreal import BOREAL
from app.modules.validaciones.obras.omint import OMINT
from app.modules.validaciones.schemas import EntradaManual


class _CtxFake:
    def __init__(self):
        self.db = object()  # nunca se usa de verdad: _validar_autorizacion_medico va mockeada
        self.fecha = None

    async def precio(self, codigo, *, exigir_admitido=True):
        return PrecioResponse(
            honorarios=Decimal("1000"), gastos=Decimal("0"), ayudante=Decimal("0"),
            descripcion="test", fuente="test", admitido=True,
        )


@pytest.fixture(autouse=True)
def _sin_chequeo_de_autorizacion_por_codigo(monkeypatch):
    """No-op: evita que `_validar_autorizacion_medico` toque la DB cuando
    `nro_validacion` viene vacío (caso Omint, que no la exige)."""
    async def _noop(db, codigo, cod_obra, autorizacion):
        return None

    monkeypatch.setattr(manual_module, "_validar_autorizacion_medico", _noop)


def _entrada(**kwargs) -> EntradaManual:
    base = dict(codigo="420101", nro_afiliado="12345", nombre_afiliado="APELLIDO NOMBRE")
    base.update(kwargs)
    return EntradaManual(**base)


# ── Configuración de las instancias reales ─────────────────────────────────────

def test_boreal_configurado_correctamente():
    assert BOREAL.nro == 285
    assert BOREAL.modalidad == "manual"
    assert BOREAL.descuenta_coseguro is True
    assert BOREAL.requiere_autorizacion is True
    assert BOREAL.requiere_nombre is True
    assert BOREAL.admite_orden is True


def test_omint_configurado_correctamente():
    assert OMINT.nro == 243
    assert OMINT.modalidad == "manual"
    assert OMINT.descuenta_coseguro is False
    assert OMINT.requiere_autorizacion is False
    assert OMINT.requiere_nombre is True
    assert OMINT.admite_orden is False


# ── Boreal: exige nro de validación, nombre, y descuenta coseguro ─────────────

@pytest.mark.asyncio
async def test_boreal_sin_nro_validacion_da_422():
    with pytest.raises(HTTPException) as exc:
        await BOREAL.validar(_CtxFake(), _entrada(nro_validacion=""))
    assert exc.value.status_code == 422
    assert "número de autorización" in exc.value.detail


@pytest.mark.asyncio
async def test_boreal_sin_nombre_afiliado_da_422():
    with pytest.raises(HTTPException) as exc:
        await BOREAL.validar(_CtxFake(), _entrada(nro_validacion="ABC123", nombre_afiliado=""))
    assert exc.value.status_code == 422
    assert "nombre del afiliado" in exc.value.detail


@pytest.mark.asyncio
async def test_boreal_carga_ok_descuenta_coseguro():
    entrada = _entrada(nro_validacion="ABC123", coseguro=Decimal("500"))
    resultado = await BOREAL.validar(_CtxFake(), entrada)

    assert resultado.estado == "cargada"
    assert resultado.nro_autorizacion == "ABC123"
    assert resultado.coseguro == Decimal("500")
    assert resultado.nombre_afiliado == "APELLIDO NOMBRE"


@pytest.mark.asyncio
async def test_boreal_sin_coseguro_cargado_toma_cero():
    entrada = _entrada(nro_validacion="ABC123")  # coseguro default = 0
    resultado = await BOREAL.validar(_CtxFake(), entrada)
    assert resultado.coseguro == Decimal("0")


# ── Omint: no exige autorización, no descuenta coseguro, sí exige nombre ──────

@pytest.mark.asyncio
async def test_omint_sin_nro_validacion_no_falla():
    """A diferencia de Boreal, Omint no exige nro de validación."""
    entrada = _entrada(nro_validacion="")
    resultado = await OMINT.validar(_CtxFake(), entrada)
    assert resultado.estado == "cargada"
    assert resultado.nro_autorizacion is None


@pytest.mark.asyncio
async def test_omint_sin_nombre_afiliado_da_422():
    with pytest.raises(HTTPException) as exc:
        await OMINT.validar(_CtxFake(), _entrada(nombre_afiliado=""))
    assert exc.value.status_code == 422
    assert "nombre del afiliado" in exc.value.detail


@pytest.mark.asyncio
async def test_omint_nunca_descuenta_coseguro_aunque_venga_cargado():
    entrada = _entrada(nro_validacion="XYZ789", coseguro=Decimal("500"))
    resultado = await OMINT.validar(_CtxFake(), entrada)
    assert resultado.estado == "cargada"
    assert resultado.nro_autorizacion == "XYZ789"
    assert resultado.coseguro == CERO


# ── Nombre en mayúsculas, siempre ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nombre_afiliado_se_normaliza_a_mayusculas():
    entrada = _entrada(nro_validacion="ABC123", nombre_afiliado="apellido nombre")
    resultado = await BOREAL.validar(_CtxFake(), entrada)
    assert resultado.nombre_afiliado == "APELLIDO NOMBRE"


# ── Sin anulación remota, en ninguna de las dos ────────────────────────────────

@pytest.mark.asyncio
async def test_boreal_y_omint_no_tienen_anulacion_remota():
    assert type(BOREAL).anular is ValidadorOS.anular
    assert type(OMINT).anular is ValidadorOS.anular
    assert await BOREAL.anular(fila=None) is None
    assert await OMINT.anular(fila=None) is None
