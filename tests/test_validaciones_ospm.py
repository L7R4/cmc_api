"""Tests de `ValidadorOspm.validar()` — OSPM (O.S. 433).

OSPM no tiene cliente HTTP externo (valida contra el padrón local
`clientes_ospm`), así que en vez de mockear un transporte se monkeypatchea
`_buscar_afiliado`/`_duplicado` — el equivalente de mockear `ospjn.
validar_afiliado` en `test_validaciones_ospjn.py`. Sin red, sin DB real.

Desde el 2026-09-20: afiliado activo → `autorizada` (factura); afiliado
existente pero inactivo → `rechazada`, "Rechazado. Afiliado suspendido";
afiliado que no está en el padrón → `rechazada`, "Rechazado. Afiliado
inexistente" (antes tiraba 422 y no grababa nada — ver `validador.py`).
"""
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.modules.facturacion.schemas import PrecioResponse
from app.modules.validaciones.core.contrato import ValidadorOS
from app.modules.validaciones.obras.ospm.schemas import EntradaOspm
from app.modules.validaciones.obras.ospm.validador import ValidadorOspm


class _CtxFake:
    """Doble mínimo de `core.contrato.Contexto`: sólo lo que `validar()` toca."""

    def __init__(self):
        self.db = object()  # nunca se usa de verdad: _buscar_afiliado/_duplicado van mockeados
        self.fecha = None

    async def precio(self, codigo, *, exigir_admitido=True):
        return PrecioResponse(
            honorarios=Decimal("1000"), gastos=Decimal("0"), ayudante=Decimal("0"),
            descripcion="test", fuente="test", admitido=True,
        )


class _AfiliadoFake:
    def __init__(self, *, activo: bool, nombre="APELLIDO NOMBRE", cuit="20123456789"):
        self.activo = activo
        self.nombre = nombre
        self.CUIT = cuit


def _entrada(documento="30123456", codigo="420101") -> EntradaOspm:
    # `documento` tiene `validation_alias="nro_afiliado"` (mismo campo que
    # manda el front) — sin `populate_by_name`, hay que construirlo por el alias.
    return EntradaOspm(nro_afiliado=documento, codigo=codigo)


@pytest.mark.asyncio
async def test_afiliado_activo_autoriza_y_factura(monkeypatch):
    validador = ValidadorOspm()

    async def _buscar(db, doc):
        return _AfiliadoFake(activo=True)

    async def _duplicado(db, **kwargs):
        return False

    async def _generar(db):
        return "24534444"

    monkeypatch.setattr(validador, "_buscar_afiliado", _buscar)
    monkeypatch.setattr(validador, "_duplicado", _duplicado)
    monkeypatch.setattr(validador, "_generar_nro_validacion", _generar)

    resultado = await validador.validar(_CtxFake(), _entrada())

    assert resultado.estado == "autorizada"
    assert resultado.detalle.startswith("Autorizado")
    # Autorizada sí se queda con un nº de validación — generado localmente,
    # OSPM no tiene servicio en línea que emita uno (ver `_generar_nro_validacion`).
    assert resultado.nro_autorizacion == "24534444"
    assert resultado.coseguro == Decimal("0")
    assert resultado.nombre_afiliado == "APELLIDO NOMBRE"
    assert resultado.traza["padron"]["activo"] is True


@pytest.mark.asyncio
async def test_afiliado_inactivo_rechaza_suspendido(monkeypatch):
    validador = ValidadorOspm()

    async def _buscar(db, doc):
        return _AfiliadoFake(activo=False)

    async def _duplicado(db, **kwargs):
        return False

    monkeypatch.setattr(validador, "_buscar_afiliado", _buscar)
    monkeypatch.setattr(validador, "_duplicado", _duplicado)

    resultado = await validador.validar(_CtxFake(), _entrada())

    assert resultado.estado == "rechazada"
    assert resultado.detalle == "Rechazado. Afiliado suspendido"
    assert resultado.nombre_afiliado == "APELLIDO NOMBRE"
    # Un rechazo no autorizó nada: no se genera nº de validación (ni se toca
    # `ctx.db`, que en este test es un `object()` sin `.execute`).
    assert resultado.nro_autorizacion is None


@pytest.mark.asyncio
async def test_afiliado_inexistente_rechaza_sin_duplicado(monkeypatch):
    validador = ValidadorOspm()
    duplicado_llamado = False

    async def _buscar(db, doc):
        return None

    async def _duplicado(db, **kwargs):
        nonlocal duplicado_llamado
        duplicado_llamado = True
        return False

    monkeypatch.setattr(validador, "_buscar_afiliado", _buscar)
    monkeypatch.setattr(validador, "_duplicado", _duplicado)

    resultado = await validador.validar(_CtxFake(), _entrada(documento="99999999"))

    assert resultado.estado == "rechazada"
    assert resultado.detalle == "Rechazado. Afiliado inexistente"
    assert resultado.nombre_afiliado == ""
    assert resultado.nro_autorizacion is None
    assert resultado.traza["padron"] == {"documento": "99999999", "encontrado": False}
    assert duplicado_llamado is False


@pytest.mark.asyncio
async def test_dni_vacio_da_422_sin_consultar_padron(monkeypatch):
    validador = ValidadorOspm()

    async def _buscar(db, doc):
        raise AssertionError("no debería consultar el padrón con el DNI vacío")

    monkeypatch.setattr(validador, "_buscar_afiliado", _buscar)

    with pytest.raises(HTTPException) as exc:
        await validador.validar(_CtxFake(), _entrada(documento="  "))
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_duplicado_da_422_y_no_construye_resultado(monkeypatch):
    validador = ValidadorOspm()

    async def _buscar(db, doc):
        return _AfiliadoFake(activo=True)

    async def _duplicado(db, **kwargs):
        return True

    monkeypatch.setattr(validador, "_buscar_afiliado", _buscar)
    monkeypatch.setattr(validador, "_duplicado", _duplicado)

    with pytest.raises(HTTPException) as exc:
        await validador.validar(_CtxFake(), _entrada(codigo="420101"))
    assert exc.value.status_code == 422
    assert "no pueden" in exc.value.detail


@pytest.mark.asyncio
async def test_anular_sigue_siendo_no_op():
    """OSPM no tiene ningún endpoint de anulación (no es un servicio en línea:
    no hay nada remoto que avisar). Si algún día se sobreescribe `anular()`,
    este test avisa que hay que revisar la decisión."""
    validador = ValidadorOspm()
    assert type(validador).anular is ValidadorOS.anular
    assert await validador.anular(fila=None) is None


class _DbFakeMax:
    """Doble de `AsyncSession`: sólo responde al `SELECT MAX(...)` que hace
    `_generar_nro_validacion` — sin red, sin DB real."""

    def __init__(self, valor):
        self._valor = valor

    async def execute(self, _stmt):
        class _Resultado:
            def __init__(self, v):
                self._v = v

            def scalar_one(self):
                return self._v

        return _Resultado(self._valor)


@pytest.mark.asyncio
async def test_generar_nro_validacion_arranca_en_numero_inicial_sin_emitidos():
    validador = ValidadorOspm()
    nro = await validador._generar_nro_validacion(_DbFakeMax(None))
    assert nro == "24534444"


@pytest.mark.asyncio
async def test_generar_nro_validacion_incrementa_el_ultimo_emitido():
    validador = ValidadorOspm()
    nro = await validador._generar_nro_validacion(_DbFakeMax("24534444"))
    assert nro == "24534445"


@pytest.mark.asyncio
async def test_generar_nro_validacion_son_8_digitos_sin_letras():
    validador = ValidadorOspm()
    nro = await validador._generar_nro_validacion(_DbFakeMax(None))
    assert len(nro) == 8
    assert nro.isdigit()
