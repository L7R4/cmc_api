"""Sancor Salud (O.S. 411) — autorizador HL7 v2.4 sobre SOAP, en línea. Pide la
autorización y guarda el resultado, salga como salga: autorizada, rechazada o
pendiente. Las que Sancor no autorizó quedan en importe 0 y `estado='X'` — se
ven en el panel pero no entran a la factura.

El cliente de transporte (`sancor.py`, HL7/SOAP puro, sin DB) vive aparte —
ver docs/api/validaciones/sancor.md para el detalle del protocolo.
"""
from typing import Optional

from fastapi import HTTPException

from app.db.models import DetalleFacturacionCMC, ListadoMedico
from app.modules.validaciones.obras.sancor import cliente as sancor
from app.modules.validaciones.core.contrato import (
    CERO,
    Anulacion,
    Contexto,
    ResultadoValidacion,
    ValidadorOS,
)
from app.modules.validaciones.obras.sancor.routes import router as _router
from app.modules.validaciones.obras.sancor.schemas import EntradaSancor


class ValidadorSancor(ValidadorOS):
    def __init__(self):
        super().__init__(
            nro=411,
            nombre="Sancor Salud",
            entrada=EntradaSancor,
            router=_router,
            prefijo="/sancor",
        )

    def verificar_prestador(self, medico: ListadoMedico) -> None:
        if not medico.MATRICULA_PROV:
            raise HTTPException(422, "El médico no tiene matrícula provincial cargada.")

    async def validar(self, ctx: Contexto, entrada: EntradaSancor) -> ResultadoValidacion:
        """La sustitución de código (`sancor.sustituir_codigo`) corre **antes**
        de todo lo demás: código bloqueado, gestión presencial, precio y la
        consulta misma usan siempre `codigo_envio` — el código que efectivamente
        se le manda a Sancor —, nunca el que tipeó el médico. Es lo que Sancor
        termina autorizando y lo que el Colegio termina facturando; el código
        original queda en la traza (`codigo_colegio`) para reconstruir el caso.
        Chequear estas reglas sobre el código del Colegio es un bug: por ejemplo
        `070660` con especialidad 16 se sustituye a `070715`, así que el chequeo
        de gestión presencial tiene que mirar `070715` (no está en la lista) y
        no `070660` (si estuviera, mandaría a gestión presencial a un médico
        cuya especialidad Sancor sí sabe resolver en línea).
        """
        especialidades = ctx.especialidades()
        codigo_envio, codigo_colegio = sancor.sustituir_codigo(entrada.codigo, especialidades)

        if codigo_envio in sancor.CODIGOS_NO_ADMITIDOS:
            raise HTTPException(
                422,
                f"El código {entrada.codigo} no está habilitado para Sancor con esta especialidad.",
            )

        afiliado = (
            f"{entrada.nro_afiliado}/{entrada.barra_afiliado}"
            if entrada.barra_afiliado
            else entrada.nro_afiliado
        )

        # Práctica que el afiliado tiene que gestionar en la obra social: no se
        # consulta el autorizador, se deja constancia y listo (igual que el
        # legacy). Va antes de exigir precio admitido: este camino no factura
        # (importe 0, estado='X'), así que tolera que el código no tenga precio
        # vigente — mejor dejar la constancia que perder el caso por un 422.
        if codigo_envio in sancor.CODIGOS_GESTION_PRESENCIAL:
            precio_gestion = await ctx.precio(codigo_envio, exigir_admitido=False)
            return ResultadoValidacion(
                estado="pendiente",
                detalle="El paciente debe tramitar esta práctica en oficinas de Sancor.",
                codigo=codigo_envio,
                precio=precio_gestion,
                nro_afiliado=afiliado,
                nombre_afiliado="",
                nro_autorizacion=None,
                coseguro=CERO,
                traza={"codigo_colegio": codigo_colegio or entrada.codigo, "codigo_enviado": codigo_envio},
            )

        precio = await ctx.precio(codigo_envio)

        try:
            res = await sancor.autorizar(
                nro_matricula=ctx.medico.MATRICULA_PROV,
                nro_afiliado=entrada.nro_afiliado,
                barra_afiliado=entrada.barra_afiliado,
                token=entrada.token,
                codigo_prestacion=codigo_envio,
                fecha=ctx.fecha,
            )
        except sancor.SancorError as e:
            # No se llegó a pedir la autorización: no inventamos una fila autorizada.
            raise HTTPException(502, str(e)) from e

        # Error de lo que tipeó el prestador (token ya usado / inválido / vencido
        # / de largo distinto de 4) — no de la prestación. No se graba nada,
        # igual que el 422 de "falta el token" del schema: el prestador corrige
        # y reintenta.
        if res.codigo_resultado in sancor.CODIGOS_ERROR_TOKEN:
            raise HTTPException(422, res.estado_detalle or "Token inválido.")

        if res.autorizada:
            estado = "autorizada"
        elif res.requiere_gestion:
            estado = "pendiente"
        else:
            estado = "rechazada"

        return ResultadoValidacion(
            estado=estado,
            detalle=res.estado_detalle,
            codigo=codigo_envio,
            precio=precio,
            nro_afiliado=afiliado,
            nombre_afiliado=res.nombre_afiliado or "",
            nro_autorizacion=res.nro_autorizacion,
            coseguro=CERO,  # Sancor no descuenta coseguro
            traza={
                "codigo_colegio": codigo_colegio or entrada.codigo,
                "codigo_enviado": codigo_envio,
                "codigo_resultado": res.codigo_resultado,
                "modo": res.modo,
                "mensaje_enviado": res.enviado,
                "respuesta": res.crudo,
            },
        )

    async def anular(self, fila: DetalleFacturacionCMC) -> Optional[Anulacion]:
        """Si la prestación tenía una autorización de Sancor, se intenta
        anularla allá (ZQA^Z04) — es lo que hace el legacy en
        `borra_atencion_colegio_sancor.php`, para que la autorización no quede
        viva en la obra social. Si Sancor no contesta, la baja local sigue
        igual: no hay evidencia de que ese camino funcione (ver `sancor.anular`),
        y trabar la baja por eso sería peor que dejar una anulación pendiente
        para hacer a mano.
        """
        if not (fila.validacion_estado == "autorizada" and fila.autorizacion):
            return None

        traza = dict(fila.validacion_respuesta or {})
        try:
            res = await sancor.anular(nro_autorizacion=fila.autorizacion)
        except sancor.SancorError as e:
            # A diferencia de Nobis, el camino Z04 nunca tuvo una sola respuesta
            # verificada en producción (15 MB de log real, cero anulaciones): no
            # hay evidencia de que funcione. Bloquear la baja acá dejaría la
            # prestación trabada para siempre si Sancor no contesta. Se da de
            # baja igual y se deja dicho — alguien va a tener que anularla a
            # mano en Sancor si el token ya se consumió.
            traza["anulacion"] = {"pendiente_en_sancor": True, "motivo": str(e)}
            return Anulacion(traza=traza)

        traza["anulacion"] = {"modo": res.modo, "respuesta": res.crudo}
        return Anulacion(traza=traza, detalle=res.estado_detalle[:255])
