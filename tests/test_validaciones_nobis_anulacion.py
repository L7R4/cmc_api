"""La baja de una prestación de Nobis exige confirmación del WSGeCROS.

Bug real, encontrado auditando el módulo antes de salir a producción:
`ValidadorNobis.anular()` llamaba a `nobis.anular_orden()` pero nunca miraba
si la respuesta era `Estado=OK`. Un `ERROR` de Nobis (orden inexistente,
rechazo lógico, lo que sea que no sea una confirmación) pasaba de largo, la
prestación se borraba igual de este lado, y la orden quedaba viva en Nobis sin
que nadie se enterara — exactamente la falla que el propio docstring del
método dice que hay que evitar (ver la nota sobre `P-Pendiente` en
`obras/nobis/validador.py`).

Mismo criterio que ya tenía Sancor (`test_validaciones_sancor_anulacion.py`):
sin confirmación de la obra social no hay baja local. Estos tests existen para
que nadie vuelva a perder el chequeo sin darse cuenta.

Sin base de datos: `anular()` sólo lee atributos de la fila y llama al
cliente, que acá se mockea.
"""
import pytest
from fastapi import HTTPException

from app.modules.validaciones.obras.nobis import cliente as nobis
from app.modules.validaciones.obras.nobis.validador import ValidadorNobis


class _Fila:
    """Lo mínimo de `DetalleFacturacionCMC` que mira `anular()`."""

    def __init__(self, estado_nobis="A", cod_autorizacion="2126063", autorizacion="5585364"):
        # `validacion_estado` no importa acá: `anular()` mira la letra que
        # dejó Nobis en la traza (`traza["estado"]`), no el estado local.
        self.validacion_estado = "autorizada" if estado_nobis == "A" else "rechazada"
        self.autorizacion = autorizacion
        self.validacion_respuesta = {
            "modo": "test",
            "estado": estado_nobis,
            "cod_autorizacion": cod_autorizacion,
        }


def _respuesta_anulacion(ok: bool, detalle: str) -> nobis.RespuestaNobis:
    return nobis.RespuestaNobis(
        autorizada=False,
        estado_detalle=detalle,
        estado="OK" if ok else "ERROR",
        crudo="<DocumentElement>...</DocumentElement>",
        modo="test",
    )


@pytest.fixture
def validador():
    return ValidadorNobis()


@pytest.mark.asyncio
async def test_error_de_nobis_no_permite_borrar(validador, monkeypatch):
    """El caso que el bug dejaba pasar: Nobis contesta pero no confirma."""

    async def _anular_orden(**_):
        return _respuesta_anulacion(False, "Orden no encontrada")

    monkeypatch.setattr(nobis, "anular_orden", _anular_orden)

    with pytest.raises(HTTPException) as exc:
        await validador.anular(_Fila())

    assert exc.value.status_code == 409
    assert "no se eliminó" in exc.value.detail
    assert "Orden no encontrada" in exc.value.detail


@pytest.mark.asyncio
async def test_confirmacion_de_nobis_deja_borrar(validador, monkeypatch):
    """Único camino que devuelve una `Anulacion` en vez de levantar: `Estado=OK`."""

    async def _anular_orden(**_):
        return _respuesta_anulacion(True, "Orden Anulada")

    monkeypatch.setattr(nobis, "anular_orden", _anular_orden)

    anulacion = await validador.anular(_Fila())

    assert anulacion is not None
    assert anulacion.detalle == "Orden Anulada"
    # La traza previa de la prestación se conserva: `anulacion` se agrega, no pisa.
    assert anulacion.traza["cod_autorizacion"] == "2126063"
    assert anulacion.traza["anulacion"]["modo"] == "test"


@pytest.mark.asyncio
async def test_pendiente_tambien_exige_confirmacion_para_borrar(validador, monkeypatch):
    """`P-Pendiente` tiene orden viva en Nobis igual que `A-Autorizado` — el
    chequeo de confirmación tiene que aplicar a las dos letras."""

    async def _anular_orden(**_):
        return _respuesta_anulacion(False, "No se puede anular")

    monkeypatch.setattr(nobis, "anular_orden", _anular_orden)

    with pytest.raises(HTTPException) as exc:
        await validador.anular(_Fila(estado_nobis="P"))

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_falla_de_transporte_tampoco_permite_borrar(validador, monkeypatch):
    """No sabemos en qué quedó la orden en Nobis: no borrar deja las dos puntas
    coherentes (ya cubierto antes del fix, se conserva como regresión)."""

    async def _anular_orden(**_):
        raise nobis.NobisError("Nobis no respondió a tiempo.")

    monkeypatch.setattr(nobis, "anular_orden", _anular_orden)

    with pytest.raises(HTTPException) as exc:
        await validador.anular(_Fila())

    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_sin_cod_autorizacion_se_borra_igual_pero_queda_marcado(validador):
    """No hay forma de anularla en Nobis sin `cod_autorizacion`: no se puede
    llamar a `AnularOrdenNroCod` — se corta antes, y esto no pasa por el chequeo
    de confirmación porque no hay nada que confirmar."""
    fila = _Fila(cod_autorizacion="")

    anulacion = await validador.anular(fila)

    assert anulacion is not None
    assert anulacion.traza["anulacion"]["pendiente_en_nobis"] is True


@pytest.mark.asyncio
async def test_sin_orden_viva_no_se_consulta_a_nobis(validador):
    """`R-Rechazada` nunca tuvo orden en Nobis: se borra sin preguntar. Si esto
    consultara, `anular_orden` fallaría por falta de `cod_autorizacion`."""
    fila = _Fila(estado_nobis="R", cod_autorizacion="")

    anulacion = await validador.anular(fila)

    assert anulacion is None
