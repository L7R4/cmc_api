"""La baja de una prestación de Sancor exige confirmación de la obra social.

Regla vigente desde el 2026-08-19, por pedido del Colegio: `ValidadorSancor.
anular()` levanta un 409 si Sancor no confirma el Z04, y `core/pipeline.py::
eliminar_prestacion()` corta ahí — con la fila intacta, porque la llamada corre
antes de cualquier mutación y antes del commit. Las dos puntas se mueven juntas
o no se mueve ninguna.

Antes era al revés: la baja local nunca se bloqueaba y lo que Sancor rechazaba
quedaba marcado `pendiente_en_sancor` para anularlo a mano. Estos tests existen
para que nadie vuelva a ese comportamiento sin darse cuenta.

⚠️ Contra Sancor test, TODO Z04 vuelve `M227^No se puede anular una autorización
facturada` (medido el 2026-08-19 sobre la autorización 125225529, emitida dos
minutos antes). O sea que hoy, con esta regla, ninguna prestación autorizada de
Sancor se puede eliminar. Es un problema a resolver con la obra social, no un
bug de acá — ver `docs/api/validaciones/sancor.md`.

Sin base de datos: `anular()` sólo lee atributos de la fila y llama al cliente,
que acá se mockea.
"""
import pytest
from fastapi import HTTPException

from app.modules.validaciones.obras.sancor import cliente as sancor
from app.modules.validaciones.obras.sancor.validador import ValidadorSancor


class _Fila:
    """Lo mínimo de `DetalleFacturacionCMC` que mira `anular()`."""

    def __init__(self, validacion_estado="autorizada", autorizacion="125225529"):
        self.validacion_estado = validacion_estado
        self.autorizacion = autorizacion
        self.validacion_respuesta = {"modo": "test", "codigo_resultado": "B000"}


def _respuesta(autorizada: bool, codigo: str, detalle: str) -> sancor.RespuestaSancor:
    return sancor.RespuestaSancor(
        autorizada=autorizada,
        estado_detalle=detalle,
        codigo_resultado=codigo,
        crudo="MSH|...\rZAU||125225529|" + f"{codigo}^{detalle}",
        modo="test",
    )


@pytest.fixture
def validador():
    return ValidadorSancor()


@pytest.mark.asyncio
async def test_rechazo_de_sancor_no_permite_borrar(validador, monkeypatch):
    """El caso real: `M227`. Sancor entendió el pedido (`MSA|AA`) y se negó."""

    async def _anular(**_):
        return _respuesta(False, "M227", "No se puede anular una autorización facturada")

    monkeypatch.setattr(sancor, "anular", _anular)

    with pytest.raises(HTTPException) as exc:
        await validador.anular(_Fila())

    assert exc.value.status_code == 409
    assert "no se eliminó" in exc.value.detail
    # El motivo de Sancor tiene que llegarle al prestador, no un texto genérico.
    assert "No se puede anular una autorización facturada" in exc.value.detail


@pytest.mark.asyncio
async def test_sancor_sin_respuesta_tampoco_permite_borrar(validador, monkeypatch):
    """`SancorError` = no sabemos en qué quedó. Borrar sería apostar a que la
    anulación salió; no borrar deja las dos puntas coherentes."""

    async def _anular(**_):
        raise sancor.SancorError("Sancor no respondió a tiempo.")

    monkeypatch.setattr(sancor, "anular", _anular)

    with pytest.raises(HTTPException) as exc:
        await validador.anular(_Fila())

    assert exc.value.status_code == 409
    assert "no se eliminó" in exc.value.detail


@pytest.mark.asyncio
async def test_confirmacion_de_sancor_deja_borrar(validador, monkeypatch):
    """Único camino que devuelve una `Anulacion` en vez de levantar: ZAU-3 `B*`."""

    async def _anular(**_):
        return _respuesta(True, "B000", "ANULADA")

    monkeypatch.setattr(sancor, "anular", _anular)

    anulacion = await validador.anular(_Fila())

    assert anulacion is not None
    assert anulacion.traza["anulacion"]["pendiente_en_sancor"] is False
    assert anulacion.traza["anulacion"]["codigo"] == "B000"
    # La traza previa de la prestación se conserva: `anulacion` se agrega, no pisa.
    assert anulacion.traza["codigo_resultado"] == "B000"
    assert anulacion.detalle == "ANULADA"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "estado, autorizacion",
    [
        ("rechazada", None),
        ("rechazada", "0"),
        ("autorizada", None),
    ],
)
async def test_sin_autorizacion_viva_no_se_consulta_a_sancor(
    validador, monkeypatch, estado, autorizacion
):
    """No hay nada que anular allá: se borra sin preguntar. Si esto consultara,
    una prestación rechazada quedaría imposible de eliminar por el M227."""

    async def _anular(**_):  # pragma: no cover - no se debe llamar
        raise AssertionError("no había autorización que anular: no hay que consultar")

    monkeypatch.setattr(sancor, "anular", _anular)

    assert await validador.anular(_Fila(estado, autorizacion)) is None
