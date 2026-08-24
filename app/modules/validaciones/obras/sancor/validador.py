"""Sancor Salud (O.S. 411) — autorizador HL7 v2.4 sobre SOAP, en línea. Pide la
autorización y guarda el resultado, salga como salga. Las que Sancor no
autorizó quedan en importe 0 y `estado='X'` — se ven en el panel pero no
entran a la factura.

Qué código se le manda a Sancor lo decide `obras/sancor/homologador.py`; lo
que se cotiza y se graba es siempre el del Colegio.

El cliente de transporte (`sancor.py`, HL7/SOAP puro, sin DB) vive aparte —
ver docs/api/validaciones/sancor.md para el detalle del protocolo.
"""
from typing import Optional

from fastapi import HTTPException

from app.db.models import DetalleFacturacionCMC, ListadoMedico
from app.modules.validaciones.obras.sancor import cliente as sancor
from app.modules.validaciones.obras.sancor import homologador
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

    def homologar(self, codigo: str, especialidad: Optional[int]) -> tuple[str, Optional[str]]:
        """Tabla de `obras/sancor/homologador.py`, por especialidad principal."""
        return homologador.homologar(codigo, especialidad)

    async def validar(self, ctx: Contexto, entrada: EntradaSancor) -> ResultadoValidacion:
        """La homologación decide **sólo con qué código se le habla a Sancor**.

        Todo lo demás —el precio, lo que se graba en `cod_nom` y lo que el
        Colegio factura— usa el código del Colegio, el que eligió el médico. El
        homologado viaja en el mensaje HL7 y queda anotado en la traza como
        `codigo_enviado`.

        Antes era al revés: se cotizaba y se grababa el sustituto, mientras que
        el buscador de códigos cotizaba el del Colegio. Un médico con
        especialidad 41 elegía `420302`, veía $ 0,00 y se le facturaban $ 27.560
        de `420351`.

        Los dos catálogos de reglas se evalúan sobre el código **del Colegio**,
        que es el que el médico eligió y sobre el que se le responde:
        `CODIGOS_NO_ADMITIDOS` son códigos que Sancor no autoriza por esta vía
        para nadie, y `CODIGOS_GESTION_PRESENCIAL` los que el afiliado tiene que
        tramitar en oficinas. Ninguno de los dos códigos homologados hoy
        (`420351`, `305001`, `420101`) está en esas listas, así que el orden no
        cambia ningún resultado.
        """
        codigo_envio, codigo_colegio = self.homologar(
            entrada.codigo, ctx.especialidad_principal()
        )

        if entrada.codigo in sancor.CODIGOS_NO_ADMITIDOS:
            # `CODIGOS_NO_ADMITIDOS` no depende de la especialidad: son códigos
            # que Sancor no autoriza por esta vía, para cualquier médico. El
            # mensaje decía "con esta especialidad" y mandaba al socio a buscar
            # un problema en su legajo que no existe.
            raise HTTPException(
                422,
                f"Sancor no autoriza en línea el código {entrada.codigo}. "
                "Consultá en el Colegio cómo cargar esta práctica.",
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
        if entrada.codigo in sancor.CODIGOS_GESTION_PRESENCIAL:
            precio_gestion = await ctx.precio(entrada.codigo, exigir_admitido=False)
            return ResultadoValidacion(
                # Mismo criterio que el M024 de más abajo: queda como rechazada
                # aunque nunca se haya consultado al autorizador. Si este camino
                # siguiera siendo "pendiente", el operador vería igual un chip
                # "Pendiente" en Sancor y volvería la ambigüedad que se quiso
                # sacar.
                estado="rechazada",
                detalle=(
                    "Pendiente de autorización de la obra social. El paciente debe "
                    "tramitar esta práctica en oficinas de Sancor."
                ),
                codigo=entrada.codigo,
                precio=precio_gestion,
                nro_afiliado=afiliado,
                nombre_afiliado="",
                nro_autorizacion=None,
                coseguro=CERO,
                traza={"codigo_colegio": codigo_colegio or entrada.codigo, "codigo_enviado": codigo_envio},
            )

        # El precio es el del código del Colegio, no el del homologado: es lo
        # que se factura y lo que el buscador ya le mostró al médico.
        precio = await ctx.precio(entrada.codigo)

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
            estado, detalle = "autorizada", res.estado_detalle
        elif res.requiere_gestion:
            # Sancor no la rechaza: la deja a la espera de que el afiliado la
            # gestione (M024, M087, M233, M030, M026, M144). Para el Colegio eso
            # es lo mismo que un rechazo —no se factura, importe 0, fuera de la
            # factura— y un estado propio "pendiente" invitaba a leerlo como
            # "todavía puede salir". Se guarda como rechazada, con el motivo
            # adelante para que dentro de seis meses se entienda que no fue un
            # rechazo de la práctica sino una autorización que quedó pendiente.
            #
            # `requiere_gestion` y CODIGOS_PENDIENTE se conservan: siguen
            # distinguiendo POR QUÉ, y el código de Sancor queda en la traza.
            estado = "rechazada"
            detalle = f"Pendiente de autorización de la obra social. {res.estado_detalle}"
        else:
            estado, detalle = "rechazada", res.estado_detalle

        return ResultadoValidacion(
            estado=estado,
            detalle=detalle,
            codigo=entrada.codigo,
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
        """Anula la autorización en Sancor (ZQA^Z04) **antes** de dar de baja la
        prestación acá — es lo que hace el legacy en
        `borra_atencion_colegio_sancor.php`.

        **Sin confirmación de Sancor no hay baja local.** Si Sancor no contesta,
        o contesta rechazando, esto levanta un 409 y `core/pipeline.py::
        eliminar_prestacion()` corta antes de tocar la fila: la prestación queda
        exactamente como estaba. Las dos puntas se mueven juntas o no se mueve
        ninguna.

        Tres finales:

        1. Sancor confirma (ZAU-3 `B*`) → se anula allá y acá.
        2. Sancor contesta pero **rechaza** (ZAU-3 `M*`) → 409, no se borra nada.
        3. Sancor no contesta (`SancorError`) → no sabemos en qué quedó → 409,
           no se borra nada.

        El criterio anterior era el opuesto (la baja local nunca se bloqueaba, y
        lo que Sancor rechazaba quedaba marcado `pendiente_en_sancor` para
        anularlo a mano). Se invirtió por pedido del Colegio el 2026-08-19: una
        prestación dada de baja acá y viva en Sancor es una autorización
        fantasma que nadie reconcilia.

        ⚠️ **Consecuencia operativa, medida contra Sancor test el 2026-08-19:**
        *todo* Z04 vuelve con `M227^No se puede anular una autorización
        facturada`, incluso sobre una autorización emitida dos minutos antes
        (probado sobre la 125225529). Con esta regla, en el entorno de test
        **ninguna prestación autorizada de Sancor se puede eliminar**. El mensaje
        no tiene ningún problema de formato —Sancor lo entiende: contesta
        `MSA|AA` y devuelve el mismo número de autorización—, es un rechazo de
        negocio. Hay que resolverlo con Sancor antes de pasar a producción.

        Las filas `rechazada` (sin número de autorización) no tienen nada que
        anular allá y se siguen borrando sin consultar nada.
        """
        if not (fila.validacion_estado == "autorizada" and fila.autorizacion):
            return None

        traza = dict(fila.validacion_respuesta or {})
        try:
            res = await sancor.anular(nro_autorizacion=fila.autorizacion)
        except sancor.SancorError as e:
            # No sabemos si Sancor llegó a procesar el pedido. Borrar acá sería
            # apostar a que sí; no borrar deja las dos puntas coherentes y el
            # prestador puede reintentar.
            raise HTTPException(
                409,
                "No pudimos confirmar la anulación con Sancor, así que la "
                f"prestación no se eliminó. {e} Reintentá en unos minutos.",
            ) from e

        if not res.autorizada:
            # Rechazo lógico. `res.autorizada` es el prefijo `B` de ZAU-3, la
            # misma regla con la que se clasifica una autorización.
            #
            # OJO — que un Z04 exitoso vuelva con `B*` es simetría con el Z02,
            # no una confirmación: no hay una sola respuesta afirmativa a un Z04
            # ni en las pruebas ni en los 15 MB de log real. Si aparece una con
            # otro prefijo, ajustar acá.
            motivo = res.estado_detalle or f"código {res.codigo_resultado}"
            raise HTTPException(
                409,
                f"Sancor no autorizó la anulación ({motivo}), así que la "
                "prestación no se eliminó. La autorización sigue vigente en la "
                "obra social; consultá en el Colegio.",
            )

        traza["anulacion"] = {
            "pendiente_en_sancor": False,
            "modo": res.modo,
            "codigo": res.codigo_resultado,
            "respuesta": res.crudo,
        }
        return Anulacion(traza=traza, detalle=res.estado_detalle[:255])
