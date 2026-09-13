"""Nobis Salud (O.S. 62) — WSGeCROS. Inserta una orden real (`InsertarAutorizacionAmb`)
y traduce el estado que devuelve Nobis. `anular()` da de baja la orden
(`AnularOrdenNroCod`).

El número es **62**, no 402: es el que tiene en `obras_sociales`, bajo el que
están cargados sus precios y el único que conoce el sistema viejo. Ver el
comentario de `__init__` antes de cambiarlo.
"""
from typing import Optional

from fastapi import HTTPException

from app.db.models import DetalleFacturacionCMC, ListadoMedico
from app.modules.validaciones.obras.nobis import cliente as nobis
from app.modules.validaciones.core.contrato import (
    CERO,
    Anulacion,
    Contexto,
    ResultadoValidacion,
    ValidadorOS,
)
from app.modules.validaciones.obras.nobis.routes import router as _router
from app.modules.validaciones.obras.nobis.schemas import EntradaNobis

# Letras de `<Estado>` con las que Nobis **creó la orden** de verdad: hay algo
# que dar de baja allá. Es lo que decide si `anular()` tiene trabajo, y no
# `validacion_estado`, que desde que el `P-Pendiente` se graba como `rechazada`
# ya no distingue una orden viva de un rechazo liso.
_LETRAS_CON_ORDEN = frozenset({nobis.ESTADO_AUTORIZADO, nobis.ESTADO_PENDIENTE})

# Filas anteriores a ese cambio: ahí `validacion_estado` sí era el discriminador.
_ESTADOS_CON_ORDEN_HISTORICOS = frozenset({"autorizada", "pendiente"})


class ValidadorNobis(ValidadorOS):
    def __init__(self):
        # 62 y no 402. El 402 llegó como número de relleno en un docstring de
        # cuando Nobis todavía no estaba implementada (commit 7081e4f, 2026-08-02)
        # y se arrastró hasta acá al implementarla. No existe en ninguna parte:
        # ni en `obras_sociales`, ni en `valor_prestacion`, ni en `nm_valores`,
        # ni en el sistema viejo. Con 402 el lookup de precios no encontraba
        # nada y **toda** carga de Nobis moría en 422 "Sin precio registrado".
        #
        # El número es sólo la clave interna del Colegio: lo que Nobis recibe
        # por el WSGeCROS son `NOBIS_COD_ENTIDAD_EFECTORA` (90692) y
        # `NOBIS_TIPO_SOLIC`, que no dependen de esto.
        super().__init__(
            nro=62,
            nombre="Nobis Salud",
            entrada=EntradaNobis,
            router=_router,
            prefijo="/nobis",
        )

    def verificar_prestador(self, medico: ListadoMedico) -> None:
        """Sin matrícula provincial, `<MatProv>` sale vacío y Nobis contesta
        "El Solicitante ingresado no existe" — 14 de 149 rechazos reales del
        `soap.log` son justo este caso. Se corta acá, antes de gastar el
        request, con el mismo criterio que Sancor
        (`obras/sancor/validador.py::verificar_prestador`)."""
        if not medico.MATRICULA_PROV:
            raise HTTPException(422, "El médico no tiene matrícula provincial cargada.")

    async def validar(self, ctx: Contexto, entrada: EntradaNobis) -> ResultadoValidacion:
        """Nobis devuelve tres estados, y los tres se graban:

        | `<Estado>` | `validacion_estado` | ¿Factura? |
        |---|---|---|
        | `A-Autorizado` | `autorizada` | sí |
        | `P-Pendiente`  | `rechazada`  | no — importe 0, `estado='X'` |
        | `R-Rechazada`  | `rechazada`  | no — importe 0, `estado='X'` |

        El **`P-Pendiente` es el caso normal** en Nobis, no una excepción: la
        orden real documentada en el legacy volvió `P-Pendiente` con su número.
        Queda esperando resolución de la obra social, así que no se puede
        facturar.

        Por eso se graba como `rechazada`, con el motivo adelante: para el
        Colegio "pendiente" y "rechazada" terminan igual —importe 0, fuera de la
        factura— y un estado propio invitaba a leerlo como "todavía puede
        salir". La letra que devolvió Nobis queda en `traza["estado"]`, que es
        lo que distingue una orden que existe allá de una que no (ver `anular`).

        En `nro_autorizacion` se guarda el **número de orden** (`Num`), que es
        lo que identifica la orden en Nobis; el código de autorización (`Cod`)
        queda en la traza, porque es lo que después pide la anulación.
        """
        precio = await ctx.precio(entrada.codigo)

        try:
            res = await nobis.insertar_autorizacion(
                numero_afiliado=entrada.nro_afiliado,
                mat_prov=str(ctx.medico.MATRICULA_PROV or ""),
                codigo_practica=entrada.codigo,
                token=entrada.token,
                cantidad=entrada.cantidad,
                fecha_prescripcion=ctx.fecha,
                fecha_realizacion=ctx.fecha,
            )
        except nobis.NobisError as e:
            # No se llegó a crear la orden: no inventamos una fila autorizada.
            raise HTTPException(502, str(e)) from e

        if res.autorizada:
            estado, detalle = "autorizada", res.estado_detalle
        elif res.requiere_gestion:
            estado = "rechazada"
            detalle = f"Pendiente de autorización de la obra social. {res.estado_detalle}"
        else:
            estado, detalle = "rechazada", res.estado_detalle

        return ResultadoValidacion(
            estado=estado,
            detalle=detalle,
            codigo=entrada.codigo,
            precio=precio,
            nro_afiliado=entrada.nro_afiliado,
            nombre_afiliado=res.nombre_afiliado or "",
            nro_autorizacion=res.nro_orden,
            # El coseguro que informa Nobis lo paga el afiliado de su bolsillo;
            # no se descuenta de lo que se le factura a la obra social. Queda
            # en la traza para que el prestador sepa cuánto cobrarle al paciente.
            coseguro=CERO,
            traza={
                "modo": res.modo,
                "estado": res.estado,
                "nro_orden": res.nro_orden,
                # Lo pide AnularOrdenNroCod: sin esto no se puede dar de baja.
                "cod_autorizacion": res.cod_autorizacion,
                "coseguro_informado": res.coseguro,
                "token_ingresado": entrada.token,
                "mensaje_enviado": res.enviado,
                "respuesta": res.crudo,
            },
        )

    async def anular(self, fila: DetalleFacturacionCMC) -> Optional[Anulacion]:
        """También hay que anular la orden allá. Ojo con la diferencia contra
        Sancor — acá NO alcanza con las autorizadas: una orden en `P-Pendiente`
        existe igual en Nobis y hay que darla de baja, si no queda viva.

        Y justamente por eso el filtro mira la **letra que devolvió Nobis**
        (`traza["estado"]`), no `validacion_estado`: desde que el `P-Pendiente`
        se graba como `rechazada`, filtrar por el estado local saltearía la baja
        de órdenes que sí existen en Nobis — la falla más cara posible acá,
        porque quedan vivas sin que nadie se entere.

        **Sin confirmación de Nobis no hay baja local.** Mismo criterio que
        Sancor (ver `obras/sancor/validador.py::anular`): si `AnularOrdenNroCod`
        no vuelve `Estado=OK` —rechazo lógico o lo que sea que Nobis conteste
        que no sea un OK—, esto levanta un 409 y `core/pipeline.py::
        eliminar_prestacion()` corta antes de tocar la fila. Antes esto no se
        chequeaba: un `ERROR` de Nobis pasaba de largo, la prestación se
        borraba acá igual, y la orden quedaba viva allá sin que nadie se
        enterara — exactamente la falla que el párrafo de arriba dice que hay
        que evitar.

        `interpretar_anulacion()` ya trata "Orden Anulada" y "Orden ya
        Anulada" como `OK` (busca "ANULADA" en el mensaje), así que reintentar
        la baja de una orden que Nobis ya había dado de baja no queda trabado
        acá.
        """
        traza = dict(fila.validacion_respuesta or {})
        letra = (traza.get("estado") or "").strip().upper()
        hay_orden = (
            letra in _LETRAS_CON_ORDEN
            or fila.validacion_estado in _ESTADOS_CON_ORDEN_HISTORICOS
        )
        if not hay_orden:
            return None
        # AnularOrdenNroCod exige pCodAut; el número de orden es opcional.
        cod_aut = (traza.get("cod_autorizacion") or "").strip()
        if not cod_aut:
            # Sin cod_aut no hay forma de anularla en Nobis. Se da de baja acá
            # igual —si no, la prestación queda trabada para siempre— pero se
            # deja dicho, porque alguien va a tener que anularla a mano.
            traza["anulacion"] = {
                "pendiente_en_nobis": True,
                "motivo": "La orden no guardó cod_autorizacion: anular manualmente en Nobis.",
            }
            return Anulacion(traza=traza)

        try:
            res = await nobis.anular_orden(cod_autorizacion=cod_aut, nro_orden=fila.autorizacion or "")
        except nobis.NobisError as e:
            raise HTTPException(
                502, f"No se pudo anular la orden en Nobis, así que no se eliminó: {e}"
            ) from e

        if res.estado != "OK":
            # Nobis contestó pero no confirmó la baja (rechazo lógico, orden
            # inexistente, lo que sea): no se toca la fila de este lado. Sin
            # este chequeo la prestación se borraba igual y la orden quedaba
            # viva en Nobis sin ningún rastro de que hiciera falta anularla a
            # mano.
            raise HTTPException(
                409,
                "Nobis no confirmó la anulación de la orden, así que la "
                f"prestación no se eliminó: {res.estado_detalle}",
            )

        traza["anulacion"] = {"modo": res.modo, "respuesta": res.crudo}
        return Anulacion(traza=traza, detalle=res.estado_detalle[:255])
