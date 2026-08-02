"""Cliente del autorizador de Sancor Salud (O.S. 411).

Sancor expone un servicio SOAP que transporta un mensaje **HL7 v2.4** en el
cuerpo. Este módulo se ocupa sólo de eso: armar el mensaje, mandarlo y traducir
la respuesta a un resultado tipado. No sabe nada de la base de datos.

⚠️ SEGURIDAD OPERATIVA
──────────────────────
Una autorización acá es un efecto real en el sistema de Sancor: consume el
token de la credencial del afiliado y genera un número de autorización. Por eso:

* `SANCOR_MODO` arranca en **"simulado"** y no sale ningún request hasta que
  alguien lo cambie a propósito;
* "test" pega contra `testservicios...` con processing-ID `D`;
* "produccion" pega contra `servicios...` con processing-ID `P`;
* toda anulación (Z04) es igual de real: da de baja la autorización en Sancor.

El sistema legacy sigue funcionando en paralelo y no se toca. Ojo con validar
dos veces la misma prestación desde los dos sistemas.
"""
import datetime
import logging
import random
import re
from dataclasses import dataclass, field
from typing import Optional
from xml.sax.saxutils import escape

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

MODO_SIMULADO = "simulado"
MODO_TEST = "test"
MODO_PRODUCCION = "produccion"


@dataclass
class RespuestaSancor:
    """Resultado normalizado de una consulta al autorizador."""

    autorizada: bool
    # Texto tal cual lo devolvió Sancor (o el motivo del rechazo).
    estado_detalle: str
    nro_autorizacion: Optional[str] = None
    nombre_afiliado: Optional[str] = None
    # `True` cuando la práctica hay que gestionarla en oficinas de la O.S.
    requiere_gestion: bool = False
    # Mensaje HL7 crudo, para soporte. No se expone al prestador.
    crudo: str = ""
    modo: str = MODO_SIMULADO
    enviado: str = field(default="", repr=False)


class SancorError(Exception):
    """Falla de transporte o respuesta ininteligible del autorizador."""


# ── Sustituciones de código por especialidad ──────────────────────────────────
# Sancor no acepta ciertos códigos del nomenclador tal cual: según la
# especialidad del efector hay que mandar otro. Replicado de `validarsancor_1.php`.
#
# OJO — inconsistencia del legacy: para 420130 + especialidad 33, la primera
# rama del PHP manda 420351 (con 305001 comentado) y las otras tres mandan
# 305001. Acá se unificó en 305001, que es lo que hacen 3 de las 4 ramas.
# Si Sancor espera 420351, cambiar el valor de esta tabla.
SUSTITUCIONES: dict[tuple[str, int], str] = {
    ("420302", 41): "420351",
    ("420130", 33): "305001",
    ("070660", 16): "070715",
}

# Códigos que Sancor no autoriza por esta vía.
CODIGOS_NO_ADMITIDOS = {"180127"}

# El afiliado tiene que tramitarla en las oficinas de Sancor.
CODIGOS_GESTION_PRESENCIAL = {"070660"}


def sustituir_codigo(codigo: str, especialidades: list[int]) -> tuple[str, Optional[str]]:
    """Devuelve (código a enviar, código original si hubo sustitución)."""
    for esp in especialidades:
        reemplazo = SUSTITUCIONES.get((codigo, esp))
        if reemplazo:
            return (reemplazo, codigo)
    return (codigo, None)


# ── Armado del mensaje ────────────────────────────────────────────────────────

def _control_id() -> str:
    """ID de control único del mensaje (campo MSH-10).

    El legacy manda siempre el mismo string hardcodeado; acá se genera uno por
    mensaje, que es lo que pide HL7 y facilita rastrear un caso con Sancor.
    """
    ahora = datetime.datetime.now()
    return f"{ahora:%y%m%d%H%M%S}{random.randint(10_000_000, 99_999_999)}"


def construir_mensaje_autorizacion(
    *,
    nro_matricula: int,
    nro_afiliado: str,
    barra_afiliado: str,
    token: str,
    codigo_prestacion: str,
    fecha: datetime.date,
    processing_id: str,
) -> str:
    """Mensaje HL7 v2.4 ZQA^Z02 (solicitud de autorización)."""
    f = f"{fecha:%Y%m%d}"
    ts = f"{datetime.datetime.now():%Y%m%d%H%M%S}"
    cuit = settings.SANCOR_CUIT

    # El separador de escape `\&` va tal cual en el segmento MSH.
    return "\n".join(
        [
            f"MSH|^~\\&|TRIT0100M|TRIT00999999|SANCOR_SALUD|SANCOR_SALUD^604940^IIN|"
            f"{ts}||ZQA^Z02^ZQA_Z02|{_control_id()}|{processing_id}|2.4|||NE|AL|ARG",
            f"AUT||||{f}|{f}|||0|0",
            f"PRD|PS^Prestador Solicitante||||||{cuit}^CU|",
            f"PRD|PL^LugarRealizacion||||||{cuit}^CU|",
            f"PRD|PE^Prestador Efector~0^^HL70454||^^^W||||{nro_matricula}^MP|",
            f"PRD|PR^Prestador Prescriptor||^^^W||||{nro_matricula}^MP|",
            f"PID|||{nro_afiliado}^{barra_afiliado}^{token}^SANCOR_SALUD^HC^SANCOR_SALUD||UNKNOWN",
            "DG1|0||Z111^CONSULTA^I10|||W",
            f"PR1|0||{codigo_prestacion}^^NM",
            "AUT|0|||||||1",
            "PV1||||P|||||||||||||||||||||||||||||||||||||||||||||||",
        ]
    )


def construir_mensaje_anulacion(*, nro_autorizacion: str, nro_matricula: int, processing_id: str) -> str:
    """Mensaje HL7 v2.4 ZQA^Z04 (anulación de una autorización)."""
    ts = f"{datetime.datetime.now():%Y%m%d%H%M%S}"
    return (
        f"MSH|^~\\&|SANCOR_SALUD|SANCOR_SALUD^604940^IIN|SANCOR_SALUD|"
        f"SANCOR_SALUD^604940^IIN|{ts}||ZQA^Z04^ZQA_Z04|{_control_id()}|"
        f"{processing_id}|2.4|||NE|AL|ARG "
        f"ZAU||{nro_autorizacion} "
        f"PRD|PS^Prestador Solicitante||||||{nro_matricula}^PR"
    )


def _envolver_soap(mensaje: str, pasaporte: str) -> str:
    return (
        "<soapenv:Envelope "
        "xmlns:soapenv='http://schemas.xmlsoap.org/soap/envelope/' "
        "xmlns:ser='http://servicio.hl7v24.sancorsalud.com.ar/'>"
        "<soapenv:Header/><soapenv:Body><ser:Message>"
        f"<pasaporte>{pasaporte}</pasaporte>"
        f"<mensaje>{escape(mensaje)}</mensaje>"
        "</ser:Message></soapenv:Body></soapenv:Envelope>"
    )


# ── Lectura de la respuesta ───────────────────────────────────────────────────

def _texto_estado(respuesta: str) -> str:
    """Motivo que devolvió Sancor.

    El legacy compara la respuesta contra ~70 strings fijos y, si no coincide
    ninguno, no guarda nada ni avisa. Acá se lee el texto del segmento que lo
    trae (ZAU/MSA/ERR) y, si no se puede, se devuelve un genérico — pero nunca
    se pierde el caso en silencio.
    """
    # ZAU-4/ZAU-5 suele traer el código y la descripción del resultado.
    m = re.search(r"ZAU\|[^\r\n]*", respuesta)
    if m:
        campos = m.group(0).split("|")
        # El texto descriptivo es el campo más largo con letras.
        candidatos = [c.strip() for c in campos if re.search(r"[A-Za-zÁÉÍÓÚáéíóúÑñ]{4,}", c)]
        if candidatos:
            return max(candidatos, key=len)[:250]

    for seg in ("ERR", "MSA"):
        m = re.search(rf"{seg}\|[^\r\n]*", respuesta)
        if m:
            campos = [c.strip() for c in m.group(0).split("|") if c.strip()]
            if len(campos) > 1:
                return " ".join(campos[1:])[:250]

    return "Sancor no devolvió un motivo reconocible."


def interpretar_respuesta(respuesta: str) -> RespuestaSancor:
    """Traduce el HL7 de vuelta a un resultado tipado."""
    texto = respuesta or ""

    autorizada = "AUTORIZADA" in texto.upper() and "NO AUTORIZAD" not in texto.upper()

    nro_autorizacion = None
    nombre_afiliado = None

    if autorizada:
        # ZAU||<nro de autorización>|... — hay que cortar en el separador de
        # campo, si no se arrastra el campo siguiente.
        m = re.search(r"ZAU\|\|([^|\r\n]{1,30})", texto)
        if m:
            nro_autorizacion = m.group(1).strip()[:30]

        # El nombre del afiliado viene después del identificador de la credencial.
        m = re.search(r"HC\^\s*SANCOR_SALUD\|\|([^|\r\n^]{1,60})", texto)
        if m:
            nombre_afiliado = m.group(1).strip()[:100]

    return RespuestaSancor(
        autorizada=autorizada,
        estado_detalle=("AUTORIZADA" if autorizada else _texto_estado(texto)),
        nro_autorizacion=nro_autorizacion,
        nombre_afiliado=nombre_afiliado,
        crudo=texto[:8000],
    )


# ── Transporte ────────────────────────────────────────────────────────────────

def _destino() -> tuple[str, str, str]:
    """(url, pasaporte, processing_id) según el modo configurado."""
    modo = (settings.SANCOR_MODO or MODO_SIMULADO).strip().lower()
    if modo == MODO_PRODUCCION:
        return (settings.SANCOR_URL_PROD, settings.SANCOR_PASAPORTE_PROD, "P")
    return (settings.SANCOR_URL_TEST, settings.SANCOR_PASAPORTE_TEST, "D")


def modo_actual() -> str:
    return (settings.SANCOR_MODO or MODO_SIMULADO).strip().lower()


async def _postear(url: str, cuerpo: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=settings.SANCOR_TIMEOUT, verify=False) as cli:
            r = await cli.post(url, content=cuerpo.encode("utf-8"),
                               headers={"Content-Type": "application/xml"})
            r.raise_for_status()
            return r.text
    except httpx.TimeoutException as e:
        raise SancorError("Sancor no respondió a tiempo. Reintentá en unos minutos.") from e
    except httpx.HTTPError as e:
        raise SancorError(f"No se pudo contactar al autorizador de Sancor: {e}") from e


async def autorizar(
    *,
    nro_matricula: int,
    nro_afiliado: str,
    barra_afiliado: str,
    token: str,
    codigo_prestacion: str,
    fecha: Optional[datetime.date] = None,
) -> RespuestaSancor:
    """Pide una autorización. En modo `simulado` no sale ningún request."""
    fecha = fecha or datetime.date.today()
    url, pasaporte, processing_id = _destino()
    modo = modo_actual()

    mensaje = construir_mensaje_autorizacion(
        nro_matricula=nro_matricula,
        nro_afiliado=nro_afiliado,
        barra_afiliado=barra_afiliado,
        token=token,
        codigo_prestacion=codigo_prestacion,
        fecha=fecha,
        processing_id=processing_id,
    )

    if modo == MODO_SIMULADO:
        # Respuesta armada localmente: sirve para probar la pantalla completa
        # sin tocar Sancor. El número de autorización es obviamente falso.
        simulada = RespuestaSancor(
            autorizada=True,
            estado_detalle="AUTORIZADA (simulada — no se consultó a Sancor)",
            nro_autorizacion=f"SIM{random.randint(100000, 999999)}",
            nombre_afiliado="AFILIADO SIMULADO",
            modo=modo,
            enviado=mensaje,
        )
        log.info("Sancor en modo simulado: no se envió la autorización.")
        return simulada

    cuerpo = _envolver_soap(mensaje, pasaporte)
    log.info("Sancor [%s] → autorización código=%s matrícula=%s", modo, codigo_prestacion, nro_matricula)
    crudo = await _postear(url, cuerpo)

    resultado = interpretar_respuesta(crudo)
    resultado.modo = modo
    resultado.enviado = mensaje
    return resultado


async def anular(*, nro_autorizacion: str, nro_matricula: int) -> RespuestaSancor:
    """Anula una autorización previa (ZQA^Z04).

    Igual que `autorizar`, en modo simulado no sale nada. En test/producción
    esto **da de baja la autorización en Sancor**, no sólo en nuestra base.
    """
    url, pasaporte, processing_id = _destino()
    modo = modo_actual()

    mensaje = construir_mensaje_anulacion(
        nro_autorizacion=nro_autorizacion,
        nro_matricula=nro_matricula,
        processing_id=processing_id,
    )

    if modo == MODO_SIMULADO:
        return RespuestaSancor(
            autorizada=False,
            estado_detalle="ANULACIÓN SIMULADA (no se consultó a Sancor)",
            modo=modo,
            enviado=mensaje,
        )

    log.info("Sancor [%s] → anulación autorización=%s", modo, nro_autorizacion)
    crudo = await _postear(url, _envolver_soap(mensaje, pasaporte))
    resultado = interpretar_respuesta(crudo)
    resultado.estado_detalle = f"ANULADA EN SANCOR · {resultado.estado_detalle}"
    resultado.modo = modo
    resultado.enviado = mensaje
    return resultado
