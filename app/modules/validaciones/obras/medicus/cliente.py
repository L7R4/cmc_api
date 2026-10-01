"""Cliente de Medicus (O.S. 373) sobre el canal Traditum.

Arma el mensaje, lo manda por `_traditum/cliente.py` y traduce la respuesta a un
resultado tipado. No sabe nada de la base.

⚠️ SEGURIDAD OPERATIVA
──────────────────────
Una autorización acá es un efecto real en el sistema de Medicus, igual que en
Sancor. `MEDICUS_MODO` arranca en **"simulado"** y no sale ningún request hasta
que alguien lo cambie a propósito; las anulaciones son igual de reales.

El sistema legacy sigue funcionando en paralelo y no se toca: ojo con validar
dos veces la misma prestación desde los dos lados.
"""
import datetime
import logging
import random
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.core.config import settings
from app.modules.validaciones.obras._traditum import cliente as traditum
from app.modules.validaciones.obras._traditum import hl7
from app.modules.validaciones.obras.medicus import mensajes

log = logging.getLogger(__name__)

MODO_SIMULADO = "simulado"
MODO_TEST = "test"
MODO_PRODUCCION = "produccion"

# ── Clasificación de la respuesta ─────────────────────────────────────────────
#
# El estado real vive en ZAU-3 (`<código>^<descripción>`), no en si la palabra
# "AUTORIZADO" aparece en algún lado del mensaje. Mismo criterio que Sancor.
#
#   B000  autorizado
#   B001  autorizado parcialmente (con varias prácticas en la transacción)
#   M000  no autorizado          M005  socio inexistente
#   P245  ya existe un consumo de esa consulta en la fecha
#
# El anexo muestra cinco códigos pero no declara el catálogo cerrado, así que se
# clasifica por prefijo y no por lista: `B` autoriza, cualquier otra cosa no.
PREFIJO_AUTORIZADA = "B"

# Prefijo de los avisos que no son rechazo de la práctica sino un consumo previo.
# Se distinguen para que el detalle explique por qué no salió.
CODIGOS_CONSUMO_PREVIO = {"P245"}


class MedicusError(Exception):
    """Falla de transporte o respuesta ininteligible."""


@dataclass
class RespuestaMedicus:
    """Resultado normalizado de una transacción."""

    autorizada: bool
    # Texto tal cual lo devolvió Medicus, o el motivo del rechazo.
    estado_detalle: str
    # ZAU-2 de la práctica; es lo que hace falta después para anular.
    nro_transaccion: str | None = None
    nombre_afiliado: str = ""
    plan: str = ""
    copago: Decimal = Decimal("0")
    cantidad_aprobada: int = 0
    # Código de ZAU-3 ("B000", "M005", "P245"). "" si no se pudo leer.
    codigo_resultado: str = ""
    # Mensaje crudo, para soporte. No se le muestra al prestador.
    crudo: str = ""
    modo: str = MODO_SIMULADO
    enviado: str = field(default="", repr=False)


def modo_actual() -> str:
    return (settings.MEDICUS_MODO or MODO_SIMULADO).strip().lower()


def _simulado() -> bool:
    # El modo del canal manda sobre el de la obra social: con el canal en
    # simulado no sale nada aunque Medicus esté en produccion.
    return modo_actual() == MODO_SIMULADO or not traditum.sale_a_la_red()


def _a_decimal(valor: str) -> Decimal:
    if not valor:
        return Decimal("0")
    try:
        return Decimal(valor.replace(",", "."))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def interpretar(crudo: str) -> RespuestaMedicus:
    """Traduce una respuesta HL7 de Medicus a un resultado tipado.

    Cuando la transacción trae prácticas se usa el ZAU **de esa práctica**, no
    el de cabecera: en una autorización parcial la cabecera dice `B001` y el
    estado real de cada ítem está en su propio ZAU. Ver
    `_traditum/hl7.py::emparejar_por_practica`.
    """
    cabecera, practicas = hl7.emparejar_por_practica(crudo)
    estado = practicas[0].estado if practicas else cabecera

    codigo = estado.codigo or cabecera.codigo
    descripcion = estado.descripcion or cabecera.descripcion
    if not descripcion:
        descripcion = hl7.motivo_alternativo(crudo) or "Medicus no devolvió un motivo."

    if codigo in CODIGOS_CONSUMO_PREVIO and descripcion:
        descripcion = f"{descripcion} (consumo previo registrado en Medicus)."

    return RespuestaMedicus(
        autorizada=codigo.upper().startswith(PREFIJO_AUTORIZADA),
        estado_detalle=descripcion[:250],
        nro_transaccion=estado.transaccion or cabecera.transaccion or None,
        nombre_afiliado=hl7.nombre_afiliado(crudo),
        plan=hl7.plan_afiliado(crudo),
        copago=_a_decimal(estado.copago),
        cantidad_aprobada=practicas[0].cantidad_aprobada if practicas else 0,
        codigo_resultado=codigo,
        crudo=crudo[:8000],
    )


async def _enviar(mensaje: str) -> str:
    try:
        return await traditum.enviar(mensaje)
    except traditum.TraditumError as e:
        raise MedicusError(str(e)) from e


# ── Transacciones ─────────────────────────────────────────────────────────────

async def consultar_elegibilidad(*, nro_afiliado: str) -> RespuestaMedicus:
    """ZQI^Z01. No genera consumo, así que en simulado se puede responder que sí
    sin riesgo de ocultar un problema."""
    mensaje = mensajes.elegibilidad(nro_afiliado=nro_afiliado)

    if _simulado():
        return RespuestaMedicus(
            autorizada=True,
            estado_detalle="SOCIO VALIDO (simulado — no se consultó a Medicus)",
            nombre_afiliado="AFILIADO SIMULADO",
            modo=modo_actual(),
            enviado=mensaje,
        )

    res = interpretar(await _enviar(mensaje))
    res.modo = modo_actual()
    res.enviado = mensaje
    return res


async def autorizar(
    *,
    nro_afiliado: str,
    codigo_prestacion: str,
    cantidad: int = 1,
    fecha: datetime.date | None = None,
) -> RespuestaMedicus:
    """ZQA^Z02. En modo simulado no sale ningún request."""
    # La fecha no viaja en el Z02 (Medicus la toma del momento de la
    # transacción); se recibe para que la firma sea la misma que la de Sancor y
    # el validador no tenga que acordarse de la diferencia.
    _ = fecha
    mensaje = mensajes.autorizacion(
        nro_afiliado=nro_afiliado, codigo=codigo_prestacion, cantidad=cantidad
    )

    if _simulado():
        log.info("Medicus en modo simulado: no se envió la autorización.")
        return RespuestaMedicus(
            autorizada=True,
            estado_detalle="AUTORIZADO (simulado — no se consultó a Medicus)",
            nro_transaccion=f"SIM{random.randint(100000, 999999)}",
            nombre_afiliado="AFILIADO SIMULADO",
            codigo_resultado="B000",
            cantidad_aprobada=cantidad,
            modo=modo_actual(),
            enviado=mensaje,
        )

    log.info(
        "Medicus [%s] → autorización código=%s afiliado=%s",
        modo_actual(), codigo_prestacion, nro_afiliado,
    )
    res = interpretar(await _enviar(mensaje))
    res.modo = modo_actual()
    res.enviado = mensaje
    return res


async def anular(*, nro_transaccion: str) -> RespuestaMedicus:
    """ZQA^Z04. En test y producción esto **da de baja la autorización en
    Medicus**, no sólo en nuestra base.

    Devuelve la respuesta sin decidir nada de negocio: quién concluye si la baja
    salió bien es el validador, leyendo `autorizada` y `codigo_resultado`.
    """
    mensaje = mensajes.anulacion(nro_transaccion=nro_transaccion)

    if _simulado():
        # `autorizada=True` porque en simulado no hay nada que anular: se
        # devuelve el final feliz para poder probar el flujo de baja entero.
        return RespuestaMedicus(
            autorizada=True,
            estado_detalle="ANULACIÓN SIMULADA (no se consultó a Medicus)",
            codigo_resultado="B000",
            modo=modo_actual(),
            enviado=mensaje,
        )

    log.info("Medicus [%s] → anulación transacción=%s", modo_actual(), nro_transaccion)
    res = interpretar(await _enviar(mensaje))
    res.modo = modo_actual()
    res.enviado = mensaje
    if not res.autorizada:
        log.warning(
            "Medicus [%s] rechazó la anulación de %s: %s^%s — sigue viva allá.",
            modo_actual(), nro_transaccion, res.codigo_resultado, res.estado_detalle,
        )
    return res
