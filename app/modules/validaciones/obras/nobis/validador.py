"""Nobis Salud (O.S. 62) — WSGeCROS. Inserta una orden real (`InsertarAutorizacionAmb`)
y traduce el estado que devuelve Nobis. `anular()` da de baja la orden
(`AnularOrdenNroCod`).

El número es **62**, no 402: es el que tiene en `obras_sociales`, bajo el que
están cargados sus precios y el único que conoce el sistema viejo. Ver el
comentario de `__init__` antes de cambiarlo.
"""
from typing import Optional

from fastapi import HTTPException

from app.db.models import DetalleFacturacionCMC
from app.modules.validaciones.obras.nobis import cliente as nobis
from app.modules.validaciones.core.contrato import (
    CERO,
    Anulacion,
    Contexto,
    ResultadoValidacion,
    ValidadorOS,
)
from app.modules.validaciones.obras.nobis.schemas import EntradaNobis


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
        super().__init__(nro=62, nombre="Nobis Salud", entrada=EntradaNobis)

    async def validar(self, ctx: Contexto, entrada: EntradaNobis) -> ResultadoValidacion:
        """Nobis devuelve tres estados, y los tres se graban:

        | `<Estado>` | `validacion_estado` | ¿Factura? |
        |---|---|---|
        | `A-Autorizado` | `autorizada` | sí |
        | `P-Pendiente`  | `pendiente`  | no — importe 0, `estado='X'` |
        | `R-Rechazada`  | `rechazada`  | no — importe 0, `estado='X'` |

        El **pendiente es el caso normal** en Nobis, no una excepción: la orden
        real documentada en el legacy volvió `P-Pendiente` con su número. Queda
        esperando resolución de la obra social, así que no puede facturarse
        todavía.

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
                cantidad=entrada.cantidad,
                fecha_prescripcion=ctx.fecha,
                fecha_realizacion=ctx.fecha,
            )
        except nobis.NobisError as e:
            # No se llegó a crear la orden: no inventamos una fila autorizada.
            raise HTTPException(502, str(e)) from e

        if res.autorizada:
            estado = "autorizada"
        elif res.requiere_gestion:
            estado = "pendiente"
        else:
            estado = "rechazada"

        return ResultadoValidacion(
            estado=estado,
            detalle=res.estado_detalle,
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
        """
        if fila.validacion_estado not in ("autorizada", "pendiente"):
            return None

        traza = dict(fila.validacion_respuesta or {})
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

        traza["anulacion"] = {"modo": res.modo, "respuesta": res.crudo}
        return Anulacion(traza=traza, detalle=res.estado_detalle[:255])
