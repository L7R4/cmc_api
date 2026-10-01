"""Armado de los mensajes HL7 v2.4 que entiende Medicus (IIN 610109).

Relevado del anexo «Mensajería HL7 – Integración de Aplicaciones (Anexo
Medicus)», revisión 03 del 2020-05-07.

Tres transacciones cubren lo que necesita el panel; las otras cuatro del anexo
(notificación de práctica Z06/Z07 y las consultas de totales Z09/Z10) quedan
para cuando haga falta conciliar.

Dos detalles del protocolo que es fácil pasar por alto:

* `MSH-11` va **siempre** en `P`. A diferencia de Sancor, el entorno de prueba
  no se elige con el processing-ID sino con la URL del canal.
* Los campos `MSH-3/4/5/6` vuelven en otra posición en la respuesta que la que
  ocupan en el envío, así que el lector no puede asumir simetría.
"""
import datetime
import random

from app.core.config import settings

IIN = "610109"
APLICACION_EMISORA = "TRIA0100M"
ID_SITIO_RECEPTOR = "HL7MED"
RECEPTOR = f"MEDICUS^{IIN}^IIN"

# Valores fijos del anexo.
VERSION_HL7 = "2.4"
ENTORNO = "P"
PAIS = "ARG"
TIPO_ACEPTACION = "NE"
TIPO_APLICACION = "AL"
# PRD-3.1.4: provincia del prestador. W = Corrientes en la tabla del anexo.
PROVINCIA_CORRIENTES = "W"
# El anexo no define un catálogo de diagnósticos para el Colegio y el reporte de
# Swiss llega con la columna `icd` vacía en todas sus filas, así que no se le
# pide al médico: se manda uno fijo, igual que hace el cliente de Sancor.
DIAGNOSTICO_POR_DEFECTO = "1234.56"


def _control_id() -> str:
    """MSH-10, código de seguridad: `aammddhhmmss` + 8 dígitos al azar."""
    ahora = datetime.datetime.now()
    return f"{ahora:%y%m%d%H%M%S}{random.randint(10_000_000, 99_999_999)}"


def _ahora() -> str:
    return f"{datetime.datetime.now():%Y%m%d%H%M%S}"


def _msh(tipo_mensaje: str) -> str:
    """Cabecera común. El separador de escape `\\&` va literal en el MSH-2."""
    return (
        f"MSH|^~\\&|{APLICACION_EMISORA}|{settings.TRADITUM_SITIO_EMISOR}|"
        f"{ID_SITIO_RECEPTOR}|{RECEPTOR}|{_ahora()}||{tipo_mensaje}|"
        f"{_control_id()}|{ENTORNO}|{VERSION_HL7}|||{TIPO_ACEPTACION}|"
        f"{TIPO_APLICACION}|{PAIS}"
    )


def _prestador_solicitante() -> str:
    """PRD del Colegio. El tipo de identificador `PR` es fijo para el
    solicitante (PRD-7.1.2.1 del anexo)."""
    return (
        f"PRD|PS^Prestador Solicitante||^^^{PROVINCIA_CORRIENTES}||||"
        f"{settings.MEDICUS_PRESTADOR}^PR|"
    )


def _pid(nro_afiliado: str) -> str:
    """PID-3: identificador del afiliado, autoridad MEDICUS, tipo HC."""
    return f"PID|||{nro_afiliado}^^^MEDICUS^HC||UNKNOWN^UNKNOWN"


def _armar(*segmentos: str) -> str:
    """Un mensaje, con los segmentos separados por CRLF.

    CRLF y no sólo CR: es lo que muestran los ejemplos escapados del documento
    del canal. Ojo que el cliente de Sancor usa `\\r` a secas — no copiar ese
    detalle acá.
    """
    return "\r\n".join(segmentos)


# ── Transacciones ─────────────────────────────────────────────────────────────

def elegibilidad(*, nro_afiliado: str) -> str:
    """ZQI^Z01 — ¿el afiliado está activo? No genera ningún consumo."""
    return _armar(
        _msh("ZQI^Z01^ZQI_Z01"),
        _prestador_solicitante(),
        f"PID|||{nro_afiliado}^^^MEDICUS^HC||UNKNOWN",
    )


def autorizacion(
    *,
    nro_afiliado: str,
    codigo: str,
    cantidad: int = 1,
    diagnostico: str = DIAGNOSTICO_POR_DEFECTO,
) -> str:
    """ZQA^Z02 — solicitud de autorización de una prestación.

    `AUT-8` es la cantidad solicitada y `AUT-9` la aprobada, que en el envío va
    en 0 porque la completa Medicus en la respuesta.
    """
    return _armar(
        _msh("ZQA^Z02^ZQA_Z02"),
        _prestador_solicitante(),
        _pid(nro_afiliado),
        f"DG1|1||{diagnostico}^^I9|||W",
        f"PR1|1||{codigo}",
        f"AUT||{IIN}||||||{cantidad}|0",
        # PV1-2 `O` = ambulatorio, PV1-4 `P` = programada, PV1-51 `V` = indicador
        # de visita. Los campos del medio van vacíos.
        "PV1||O||P" + "|" * 47 + "V",
    )


def anulacion(*, nro_transaccion: str) -> str:
    """ZQA^Z04 — anulación global de una autorización.

    Se anula toda la transacción. El anexo también admite anular práctica por
    práctica repitiendo `PR1` + `ZAU` con el número particular de cada una; no
    hace falta mientras el panel cargue de a una prestación por vez.
    """
    return _armar(
        _msh("ZQA^Z04^ZQA_Z02"),
        f"ZAU||{nro_transaccion}",
        _prestador_solicitante(),
    )
