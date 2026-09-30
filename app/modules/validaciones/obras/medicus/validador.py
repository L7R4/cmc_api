"""Medicus (O.S. 373) — autorizador HL7 v2.4 sobre el canal Traditum, en línea.

Pide la autorización y guarda el resultado, salga como salga. Las que Medicus no
autorizó quedan en importe 0 y `estado='X'` — se ven en el panel pero no entran
a la factura.

El transporte (`_traditum/`) y el armado de mensajes (`mensajes.py`) viven
aparte; acá está sólo la lógica de negocio contra el contrato de `core/`.
"""
import logging
from typing import Optional

from fastapi import HTTPException

from app.db.models import DetalleFacturacionCMC
from app.modules.validaciones.core.contrato import (
    CERO,
    Anulacion,
    Contexto,
    ResultadoValidacion,
    ValidadorOS,
    factura_en_cero,
)
from app.modules.validaciones.obras.medicus import cliente as medicus
from app.modules.validaciones.obras.medicus.routes import router as _router
from app.modules.validaciones.obras.medicus.schemas import EntradaMedicus

log = logging.getLogger(__name__)

# NRO_OBRASOCIAL de `MEDICUS,CORPORATE,FAMILY -MC-`.
#
# Ojo que en `obras_sociales` hay una segunda Medicus: la 372 (MEDICUS FUERZAS
# DE SEGURIDAD, planes MS1/MS2 desde el 01/06/2026), hoy con MARCA='N'. Si el
# Colegio la activa va como otro validador con su propio `nro`, no como un plan
# de este: el catálogo del panel y el despacho de `obras.POR_NRO` son por
# NRO_OBRASOCIAL.
NRO_MEDICUS = 373


class ValidadorMedicus(ValidadorOS):
    def __init__(self):
        super().__init__(
            nro=NRO_MEDICUS,
            nombre="Medicus",
            entrada=EntradaMedicus,
            router=_router,
            prefijo="/medicus",
        )

    async def validar(self, ctx: Contexto, entrada: EntradaMedicus) -> ResultadoValidacion:
        """Pide la autorización y arma la fila con lo que haya contestado.

        `homologar()` queda con el default de identidad: el anexo dice que la
        tabla de códigos depende del convenio de cada prestador, y hasta que
        Medicus confirme la del Colegio se le manda el código tal cual lo eligió
        el médico. Si más adelante exige otros, se sobreescribe el hook y se
        agrega un `homologador.py`, como tiene Sancor.
        """
        # El precio es el del código del Colegio: es lo que se factura y lo que
        # el buscador ya le mostró al médico.
        #
        # `exigir_admitido=False` a propósito, a diferencia de Sancor: el Z02 de
        # Medicus no lleva importe, así que la obra social autoriza igual sin
        # precio cargado. Cortar con 422 dejaría al médico sin poder validar una
        # prestación que Medicus sí autoriza, por un dato que falta de este lado.
        #
        # El riesgo que advierte `core/contrato.py` sigue siendo real —una fila
        # en cero entra a la liquidación y el médico cobra de menos—, así que en
        # vez de silenciarlo se deja anotado en el detalle y en la traza para que
        # el Colegio lo vea y cargue el valor.
        precio = await ctx.precio(entrada.codigo, exigir_admitido=False)
        sin_precio = factura_en_cero(precio)

        try:
            res = await medicus.autorizar(
                nro_afiliado=entrada.nro_afiliado,
                codigo_prestacion=entrada.codigo,
                cantidad=entrada.cantidad,
                fecha=ctx.fecha,
            )
        except medicus.MedicusError as e:
            # No se llegó a pedir la autorización: no inventamos una fila
            # autorizada.
            raise HTTPException(502, str(e)) from e

        estado = "autorizada" if res.autorizada else "rechazada"

        detalle = res.estado_detalle
        if sin_precio and res.autorizada:
            detalle = (
                f"{detalle} · Sin valor cargado para el código {entrada.codigo} "
                "en esta obra social: la autorización salió, pero la prestación "
                "queda en $ 0 hasta que el Colegio cargue el valor."
            )[:250]

        return ResultadoValidacion(
            estado=estado,
            detalle=detalle,
            codigo=entrada.codigo,
            precio=precio,
            nro_afiliado=entrada.nro_afiliado,
            nombre_afiliado=res.nombre_afiliado,
            nro_autorizacion=res.nro_transaccion,
            # Medicus sí devuelve copago (ZAU-6), a diferencia de Sancor. Se
            # graba en la misma columna que usa Boreal.
            coseguro=res.copago or CERO,
            traza={
                "iin": medicus.mensajes.IIN,
                "codigo_resultado": res.codigo_resultado,
                "sin_precio": sin_precio,
                "plan": res.plan,
                "cantidad_aprobada": res.cantidad_aprobada,
                "modo": res.modo,
                "mensaje_enviado": res.enviado,
                "respuesta": res.crudo,
            },
        )

    async def anular(self, fila: DetalleFacturacionCMC) -> Optional[Anulacion]:
        """Anula la autorización en Medicus (ZQA^Z04) **antes** de dar de baja la
        prestación acá.

        Mismo criterio que Sancor, por la misma razón: una prestación borrada
        acá y viva en Medicus es una autorización fantasma que nadie reconcilia.
        Sin confirmación de la obra social esto levanta un 409 y
        `core/pipeline.py::eliminar_prestacion()` corta antes de tocar la fila.

        Las filas `rechazada` no tienen número de transacción y no hay nada que
        anular allá, así que se borran sin consultar nada.
        """
        if not (fila.validacion_estado == "autorizada" and fila.autorizacion):
            return None

        traza = dict(fila.validacion_respuesta or {})
        try:
            res = await medicus.anular(nro_transaccion=fila.autorizacion)
        except medicus.MedicusError as e:
            raise HTTPException(
                409,
                "No pudimos confirmar la anulación con Medicus, así que la "
                f"prestación no se eliminó. {e} Reintentá en unos minutos.",
            ) from e

        if not res.autorizada:
            motivo = res.estado_detalle or f"código {res.codigo_resultado}"
            raise HTTPException(
                409,
                f"Medicus no autorizó la anulación ({motivo}), así que la "
                "prestación no se eliminó. La autorización sigue vigente en la "
                "obra social; consultá en el Colegio.",
            )

        traza["anulacion"] = {
            "modo": res.modo,
            "codigo": res.codigo_resultado,
            "respuesta": res.crudo,
        }
        return Anulacion(traza=traza, detalle=res.estado_detalle[:255])
