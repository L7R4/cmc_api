"""OSPJN — Obra Social del Poder Judicial (O.S. 151). Valida al AFILIADO, no
una práctica: se le manda una *categoría* de prestación ('CON' consultas /
'OTR' el resto) y contesta si está en condiciones, con un `NroConsulta` que
acredita la validación. Por eso no hay nada que anular después: eliminar la
prestación es una baja local (`anular()` queda con el default no-op).

Hoy se manda siempre `CATEGORIA_CONSULTA` ("CON") salvo que el código tenga
homologación (ver `obras/ospjn/homologador.py`) — mismo comentario en
`validar()` sobre por qué el resto no usa `categoria_de_codigo()` todavía.
"""
from typing import Optional

from fastapi import HTTPException

from app.modules.validaciones.obras.ospjn import cliente as ospjn
from app.modules.validaciones.obras.ospjn import homologador
from app.modules.validaciones.core.contrato import CERO, Contexto, ResultadoValidacion, ValidadorOS
from app.modules.validaciones.obras.ospjn.routes import router as _router
from app.modules.validaciones.obras.ospjn.schemas import EntradaOspjn


class ValidadorOspjn(ValidadorOS):
    def __init__(self):
        super().__init__(
            nro=151, nombre="OSPJN · Poder Judicial", entrada=EntradaOspjn,
            router=_router, prefijo="/ospjn",
        )

    def homologar(self, codigo: str, especialidad: Optional[int]) -> tuple[str, Optional[str]]:
        """Tabla de `obras/ospjn/homologador.py`, por especialidad principal."""
        return homologador.homologar(codigo, especialidad)

    async def validar(self, ctx: Contexto, entrada: EntradaOspjn) -> ResultadoValidacion:
        """
        | Respuesta | `validacion_estado` | ¿Factura? |
        |---|---|---|
        | `NroConsulta` distinto de 0 | `autorizada` | sí |
        | INACTIVO / SUSPENDIDO / no encontrado | `rechazada` | no — importe 0, `estado='X'` |

        A OSPJN se le manda la categoría; el precio y lo que se guarda usan
        **siempre el código del Colegio**, el que eligió el médico — igual que
        Sancor. `codigo_colegio`/`codigo_enviado` quedan en la traza para que
        se vea cuándo hubo homologación.
        """
        codigo_envio, codigo_colegio = self.homologar(
            entrada.codigo, ctx.especialidad_principal()
        )

        precio = await ctx.precio(entrada.codigo)

        # El legacy (judicial/grabar_judiciales.php) manda SIEMPRE "CON" a OSPJN,
        # sin importar el código real de la prestación — es el único valor que se
        # probó en meses de uso real en producción. `ospjn.categoria_de_codigo()`
        # sabe derivar 'OTR' para el resto de los códigos, pero eso nunca se validó
        # contra el servicio real de OSPJN, así que por ahora no se usa para nadie
        # más. La única excepción es un código con homologación explícita (ver
        # `homologador.py`): ahí la categoría sale del código homologado, no del
        # default — es la manera de decirle a OSPJN "esto es una consulta" para
        # una práctica que por número no lo es. Si en algún momento se confirma
        # con OSPJN que 'OTR' funciona para el resto, este es el único lugar que
        # hay que tocar para reactivarlo.
        categoria = (
            ospjn.categoria_de_codigo(codigo_envio)
            if codigo_colegio is not None
            else ospjn.CATEGORIA_CONSULTA
        )

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
                "codigo_colegio": codigo_colegio or entrada.codigo,
                "codigo_enviado": codigo_envio,
                "modo": res.modo,
                "categoria_enviada": categoria,
                "estado": res.estado,
                "nro_consulta": res.nro_consulta,
                "nro_documento": res.nro_documento,
                "mensaje_enviado": res.enviado,
                "respuesta": res.crudo,
            },
        )
