"""Tests del validador de OSPJN — Obra Social del Poder Judicial (O.S. 151).

Cubre `obras/ospjn/cliente.py::interpretar()` (parseo puro de la respuesta de
`ValidarAfiliado`) y `obras/ospjn/validador.py::ValidadorOspjn.validar()` (con
`ospjn.validar_afiliado` mockeado — sin red, sin DB). La forma de la respuesta
usada en los fixtures está confirmada contra el log real de producción del
legacy (`mensajeria_judicial.txt`): campos `Estado`, `EstadoDescripcion`,
`Mensaje`, `Nombre`, `NroAfiliado`, `NroConsulta`, `NroDocumento`, `RequestId`,
`Resultado`.

No hay `test_validaciones_ospjn_anulacion.py`: OSPJN no tiene ningún endpoint
de anulación (confirmado contra el legacy, que sólo hace una baja lógica
local) — `ValidadorOspjn` no sobreescribe `anular()`, y eso se cubre acá mismo
con un test dedicado en vez de un archivo aparte.
"""
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.modules.facturacion.schemas import PrecioResponse
from app.modules.validaciones.core.contrato import ValidadorOS
from app.modules.validaciones.obras.ospjn import cliente as ospjn
from app.modules.validaciones.obras.ospjn.schemas import EntradaOspjn
from app.modules.validaciones.obras.ospjn.validador import ValidadorOspjn


# ── `interpretar()` — parseo puro, sin red ─────────────────────────────────────

def test_interpretar_autorizado():
    # Recorte real (anonimizado) de `mensajeria_judicial.txt`.
    datos = {
        "Email": None,
        "Estado": 1,
        "EstadoDescripcion": "ACTIVO",
        "FechaSuspension": None,
        "Mensaje": None,
        "Nombre": "APELLIDO NOMBRE",
        "NroAfiliado": "12345/1",
        "NroConsulta": 9443,
        "NroDocumento": 14817487,
        "RequestId": "dc7bd6ea-9307-4c22-b664-b56e7e35b940",
        "Resultado": True,
        "Telefono": None,
    }
    r = ospjn.interpretar(datos)
    assert r.validado is True
    assert r.nro_consulta == "9443"
    assert r.nombre_afiliado == "APELLIDO NOMBRE"
    assert r.estado == "ACTIVO"
    assert r.estado_detalle == "ACTIVO"


def test_interpretar_nro_consulta_cero_no_valida():
    datos = {"EstadoDescripcion": "ACTIVO", "Mensaje": None, "NroConsulta": 0}
    r = ospjn.interpretar(datos)
    assert r.validado is False
    assert r.nro_consulta is None


def test_interpretar_sin_token():
    datos = {
        "EstadoDescripcion": None,
        "Mensaje": "No se encuentra token de autenticación",
        "NroConsulta": 0,
    }
    r = ospjn.interpretar(datos)
    assert r.validado is False
    assert "token" in r.estado_detalle.lower()


def test_interpretar_afiliado_no_encontrado():
    datos = {
        "EstadoDescripcion": None,
        "Mensaje": "Afiliado NO encontrado",
        "NroConsulta": 0,
    }
    r = ospjn.interpretar(datos)
    assert r.validado is False
    assert "no encontrado" in r.estado_detalle.lower()


@pytest.mark.parametrize("estado", ["INACTIVO", "SUSPENDIDO"])
def test_interpretar_estados_no_validos(estado):
    datos = {"EstadoDescripcion": estado, "Mensaje": None, "NroConsulta": 0}
    r = ospjn.interpretar(datos)
    assert r.validado is False
    assert estado in r.estado_detalle


def test_interpretar_caso_generico_desconocido():
    # Sin `NroConsulta` válido, sin mensaje ni estado reconocidos: el fallback
    # tiene que avisar que no se sabe el motivo, no inventar uno.
    datos = {"EstadoDescripcion": None, "Mensaje": None, "NroConsulta": None}
    r = ospjn.interpretar(datos)
    assert r.validado is False
    assert r.estado_detalle == "OSPJN no informó el motivo."


# ── `categoria_de_codigo()` — se conserva pero no se usa en `validar()` ────────

@pytest.mark.parametrize("codigo,esperado", [
    ("420101", ospjn.CATEGORIA_CONSULTA),
    ("430202", ospjn.CATEGORIA_CONSULTA),
    ("110403", ospjn.CATEGORIA_OTRAS),
])
def test_categoria_de_codigo(codigo, esperado):
    assert ospjn.categoria_de_codigo(codigo) == esperado


# ── `ValidadorOspjn.validar()` — con el cliente mockeado ──────────────────────

class _CtxFake:
    """Doble mínimo de `core.contrato.Contexto`: sólo lo que `validar()` toca."""

    def __init__(self):
        self.fecha = None

    async def precio(self, codigo, *, exigir_admitido=True):
        return PrecioResponse(
            honorarios=Decimal("1000"), gastos=Decimal("0"), ayudante=Decimal("0"),
            descripcion="test", fuente="test", admitido=True,
        )


@pytest.mark.asyncio
async def test_validar_siempre_manda_categoria_con(monkeypatch):
    """Aunque el código no sea de consulta (42*/430202), lo que se envía a OSPJN
    es siempre "CON" — decisión tomada para alinearse al legacy, que nunca
    probó "OTR" en producción real."""
    capturado = {}

    async def _validar_afiliado(**kwargs):
        capturado.update(kwargs)
        return ospjn.RespuestaOspjn(
            validado=True, estado_detalle="ACTIVO", estado="ACTIVO",
            nro_consulta="123", nombre_afiliado="TEST", modo="test", enviado="{}",
        )

    monkeypatch.setattr(ospjn, "validar_afiliado", _validar_afiliado)

    validador = ValidadorOspjn()
    entrada = EntradaOspjn(codigo="110403", nro_afiliado="12345", barra_afiliado="01")
    resultado = await validador.validar(_CtxFake(), entrada)

    assert capturado["categoria_prestacion"] == ospjn.CATEGORIA_CONSULTA == "CON"
    assert resultado.estado == "autorizada"
    assert resultado.traza["categoria_enviada"] == "CON"


@pytest.mark.asyncio
async def test_validar_rechazada(monkeypatch):
    async def _validar_afiliado(**kwargs):
        return ospjn.RespuestaOspjn(
            validado=False, estado_detalle="El afiliado figura INACTIVO en OSPJN.",
            estado="INACTIVO", nro_consulta=None, modo="test", enviado="{}",
        )

    monkeypatch.setattr(ospjn, "validar_afiliado", _validar_afiliado)

    validador = ValidadorOspjn()
    entrada = EntradaOspjn(codigo="420101", nro_afiliado="12345", barra_afiliado="01")
    resultado = await validador.validar(_CtxFake(), entrada)

    assert resultado.estado == "rechazada"
    assert resultado.coseguro == Decimal("0")


@pytest.mark.asyncio
async def test_validar_error_transporte_da_502(monkeypatch):
    async def _validar_afiliado(**kwargs):
        raise ospjn.OspjnError("OSPJN no respondió a tiempo.")

    monkeypatch.setattr(ospjn, "validar_afiliado", _validar_afiliado)

    validador = ValidadorOspjn()
    entrada = EntradaOspjn(codigo="420101", nro_afiliado="12345", barra_afiliado="01")
    with pytest.raises(HTTPException) as exc:
        await validador.validar(_CtxFake(), entrada)
    assert exc.value.status_code == 502


# ── `anular()` — confirmar que sigue siendo el no-op heredado ─────────────────

@pytest.mark.asyncio
async def test_anular_sigue_siendo_no_op():
    """OSPJN no tiene ningún endpoint de anulación (confirmado contra el
    legacy: la baja es sólo local). Si algún día `ValidadorOspjn` sobreescribe
    `anular()`, este test falla y avisa que hay que revisar la decisión."""
    validador = ValidadorOspjn()
    assert type(validador).anular is ValidadorOS.anular
    assert await validador.anular(fila=None) is None
