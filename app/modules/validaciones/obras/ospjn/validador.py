"""OSPJN — Obra Social del Poder Judicial (O.S. 151). Valida al AFILIADO, no
una práctica: se le manda una *categoría* de prestación ('CON' consultas /
'OTR' el resto) y contesta si está en condiciones, con un `NroConsulta` que
acredita la validación. Por eso no hay nada que anular después: eliminar la
prestación es una baja local (`anular()` queda con el default no-op).
"""
from fastapi import HTTPException

from app.modules.validaciones.obras.ospjn import cliente as ospjn
from app.modules.validaciones.core.contrato import CERO, Contexto, ResultadoValidacion, ValidadorOS
from app.modules.validaciones.obras.ospjn.schemas import EntradaOspjn


class ValidadorOspjn(ValidadorOS):
    def __init__(self):
        super().__init__(nro=151, nombre="OSPJN · Poder Judicial", entrada=EntradaOspjn)

    async def validar(self, ctx: Contexto, entrada: EntradaOspjn) -> ResultadoValidacion:
        """
        | Respuesta | `validacion_estado` | ¿Factura? |
        |---|---|---|
        | `NroConsulta` distinto de 0 | `autorizada` | sí |
        | INACTIVO / SUSPENDIDO / no encontrado | `rechazada` | no — importe 0, `estado='X'` |

        A OSPJN se le manda la categoría; el precio y lo que se guarda usan
        **siempre el código del Colegio**.
        """
        precio = await ctx.precio(entrada.codigo)

        # Se deriva del propio código (42* + 430202 = consulta; el resto 'OTR'), no de
        # una columna: la regla es una función del número.
        categoria = ospjn.categoria_de_codigo(entrada.codigo)

        try:
            res = await ospjn.validar_afiliado(
                numero_afiliado=entrada.nro_afiliado,
                barra_afiliado=entrada.barra_afiliado,
                categoria_prestacion=categoria,
                fecha=ctx.fecha,
            )
        except ospjn.OspjnError as e:
            # No se llegó a validar: no inventamos una fila autorizada.
            raise HTTPException(502, str(e)) from e

        afiliado = (
            f"{entrada.nro_afiliado}/{entrada.barra_afiliado}"
            if entrada.barra_afiliado
            else entrada.nro_afiliado
        )

        return ResultadoValidacion(
            estado="autorizada" if res.validado else "rechazada",
            detalle=res.estado_detalle,
            codigo=entrada.codigo,
            precio=precio,
            nro_afiliado=afiliado,
            nombre_afiliado=res.nombre_afiliado or "",
            nro_autorizacion=res.nro_consulta,
            coseguro=CERO,  # OSPJN no descuenta coseguro
            traza={
                "modo": res.modo,
                "categoria_enviada": categoria,
                "estado": res.estado,
                "nro_consulta": res.nro_consulta,
                "nro_documento": res.nro_documento,
                "mensaje_enviado": res.enviado,
                "respuesta": res.crudo,
            },
        )
