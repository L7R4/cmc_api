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
import html
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
    # `True` cuando Sancor no rechazó ni autorizó: el afiliado tiene que
    # gestionarlo con la obra social (tope, autorización manual, plan no
    # convenido — ver CODIGOS_PENDIENTE). Mismo patrón que `nobis.py`.
    requiere_gestion: bool = False
    # Código de ZAU-3 (ej. "B000", "M117", "M024"). "" si no se pudo leer el
    # segmento ZAU. Es lo que `service.py` usa para clasificar autorizada /
    # pendiente / rechazada / error de token — ver CODIGOS_PENDIENTE abajo.
    codigo_resultado: str = ""
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

# Códigos que Sancor no autoriza por esta vía. Unificado con el front (antes
# 180150/180164 sólo se bloqueaban ahí, ver docs/api/validaciones/sancor.md).
CODIGOS_NO_ADMITIDOS = {"180127", "180150", "180164"}

# El afiliado tiene que tramitarla en las oficinas de Sancor.
CODIGOS_GESTION_PRESENCIAL = {"070660"}

# ── Clasificación de la respuesta real de Sancor ──────────────────────────────
# Relevado de `mensajeria_sancor.txt`: ~14.130 respuestas reales del autorizador
# en producción (2024-2026). El estado real vive en ZAU-3 ("<código>^<descripción>"),
# no en si el texto "AUTORIZADA" aparece en algún lado del mensaje.
#
#   B*     21.062 casos — autorizada (incluye variantes como "AUTORIZADO- Recuerde
#          adjuntar informe médico" y "AUTORIZADO - Sujeto a Auditoría Posterior":
#          lo que importa es el prefijo B, no el texto exacto "AUTORIZADA").
#   M117    2.042 casos — error de lo que tipeó el prestador (token ya usado,
#          inválido, vencido, de largo ≠ 4). No es un rechazo de la prestación:
#          se corta con 422 antes de grabar nada, mismo criterio que el 422 de
#          "falta el token" que ya tira validar_sancor().
#   resto (M024/M087/M233/M030/M026/M144, ~250 casos) — el afiliado tiene que
#          gestionarlo en Sancor (tope, requiere autorización manual, plan no
#          convenido). No es un rechazo: pendiente, no factura.
#   cualquier otro M*  — rechazada.

# Prefijo de "autorizada". Sancor no documenta un catálogo cerrado de códigos B*;
# en 21.062 respuestas reales todas empezaron así.
PREFIJO_AUTORIZADA = "B"

CODIGOS_ERROR_TOKEN = {"M117"}

CODIGOS_PENDIENTE = {"M024", "M087", "M233", "M030", "M026", "M144"}


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


def construir_mensaje_anulacion(*, nro_autorizacion: str, processing_id: str) -> str:
    """Mensaje HL7 v2.4 ZQA^Z04 (anulación de una autorización).

    El legacy (`borra_atencion_colegio_sancor.php`) separa los segmentos con
    espacios en vez de CR — probablemente por eso este camino nunca tiene una
    sola respuesta registrada en 15 MB de log real (`mensajeria_sancor.txt`):
    Sancor nunca llegó a interpretar el mensaje como HL7 válido. Acá se separa
    con `\\r`, igual que exige el estándar y que hace `interpretar_respuesta()`
    al leer la respuesta.

    `PRD|PS^Prestador Solicitante` lleva el código de prestador del Colegio
    (`settings.SANCOR_PRESTADOR`), no la matrícula del médico — así lo devuelve
    Sancor en cada una de las ~14.000 respuestas reales relevadas.
    """
    ts = f"{datetime.datetime.now():%Y%m%d%H%M%S}"
    return "\r".join(
        [
            f"MSH|^~\\&|SANCOR_SALUD|SANCOR_SALUD^604940^IIN|SANCOR_SALUD|"
            f"SANCOR_SALUD^604940^IIN|{ts}||ZQA^Z04^ZQA_Z04|{_control_id()}|"
            f"{processing_id}|2.4|||NE|AL|ARG",
            f"ZAU||{nro_autorizacion}",
            f"PRD|PS^Prestador Solicitante||||||{settings.SANCOR_PRESTADOR}^PR",
        ]
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
#
# La respuesta real de Sancor viene envuelta en SOAP con los segmentos HL7
# separados por `&#xD;`/`&#xd;` (CR **XML-escapado**), no por CR real:
#
#   <resultado>MSH|...|2.4|||NE|AL|ARG&#xD;MSA|AA|...&#xD;ZAU||133790447|B000^AUTORIZADA&#xD;
#   PRD|...&#xD;PID|1|59681210^^^^Doc. Nac. Identidad|2371388^01^9999^^HC||
#   IAN BENJAMIN^MARTINEZ ALFARO||20230504|M&#xD;...</resultado>
#
# Sin desescapar primero, cualquier regex con `[^\r\n]*` matchea el mensaje
# HL7 entero en vez de un segmento — por eso `_extraer_hl7()` corre antes que
# cualquier otro parseo.

def _extraer_hl7(cuerpo_soap: str) -> str:
    """Recorta `<resultado>…</resultado>` del sobre SOAP y desescapa entidades
    XML (`&#xD;` → CR, `&amp;` → `&`). Si no encuentra el tag, devuelve el
    cuerpo tal cual — mejor un mensaje sucio que perder la respuesta."""
    m = re.search(r"<resultado>(.*?)</resultado>", cuerpo_soap, re.DOTALL | re.IGNORECASE)
    crudo = m.group(1) if m else cuerpo_soap
    return html.unescape(crudo)


def _campos_segmento(hl7: str, tipo: str) -> Optional[list[str]]:
    """Campos del **último** segmento `tipo` (p.ej. "ZAU", "PID"), ya
    separados por `|`. `campos[i]` es el campo HL7 `tipo.{i+1}` — p.ej. para
    `ZAU||133790447|B000^AUTORIZADA`, `campos[1]` es el nro de autorización
    (ZAU-2) y `campos[2]` el código de resultado (ZAU-3). `None` si el
    segmento no aparece en el mensaje.

    El último y no el primero: en ~10.000 respuestas reales con una práctica
    aceptada para evaluación, Sancor manda **dos** ZAU — uno genérico "de
    cabecera" (`M000^PRESTACIONES RECHAZADAS`) y, después de PR1/AUT, el
    específico de esa práctica (`M024^REQUIERE AUTORIZACION`). Quedarse con
    el primero clasifica como rechazada una prestación que en realidad
    requiere gestión del afiliado. Cuando Sancor corta antes de llegar a
    evaluar la práctica (token inválido, afiliado inexistente) sólo hay un
    ZAU, y ese es el que se usa.
    """
    coincidencias = re.findall(rf"(?:^|\r){re.escape(tipo)}\|(.*?)(?:\r|$)", hl7)
    if not coincidencias:
        return None
    return coincidencias[-1].split("|")


def _texto_estado(hl7: str) -> str:
    """Fallback cuando ZAU no trae descripción: intenta MSA/ERR y, si
    tampoco, un genérico. Nunca se pierde el caso en silencio."""
    for seg in ("ERR", "MSA"):
        campos = _campos_segmento(hl7, seg)
        if campos:
            texto = " ".join(c.strip() for c in campos if c.strip())
            if texto:
                return texto[:250]
    return "Sancor no devolvió un motivo reconocible."


def interpretar_respuesta(respuesta_soap: str) -> RespuestaSancor:
    """Traduce la respuesta SOAP/HL7 de Sancor a un resultado tipado.

    El estado real vive en ZAU-3 (`<código>^<descripción>`, p.ej.
    `B000^AUTORIZADA` o `M117^El Token ya fue utilizado.`) — no en si la
    palabra "AUTORIZADA" aparece en algún lado del mensaje: hay ~21.000
    respuestas reales que autorizan con textos distintos
    (`B000^AUTORIZADO- Recuerde adjuntar informe médico.`) y ninguna respuesta
    de rechazo real contiene la palabra "AUTORIZADA" sola. Ver
    CODIGOS_PENDIENTE / CODIGOS_ERROR_TOKEN más arriba para la clasificación
    completa por código.
    """
    hl7 = _extraer_hl7(respuesta_soap or "")

    codigo_resultado = ""
    descripcion = ""
    nro_autorizacion = None

    campos_zau = _campos_segmento(hl7, "ZAU")
    if campos_zau and len(campos_zau) >= 3:
        # ZAU-2: "0" es el centinela de "sin autorización" que usa Sancor.
        crudo_nro = (campos_zau[1] or "").strip()
        nro_autorizacion = crudo_nro[:30] if crudo_nro and crudo_nro != "0" else None
        codigo_resultado, _, descripcion = campos_zau[2].partition("^")
        codigo_resultado = codigo_resultado.strip()
        descripcion = descripcion.strip()

    autorizada = codigo_resultado.upper().startswith(PREFIJO_AUTORIZADA)

    if not descripcion:
        descripcion = _texto_estado(hl7)

    # PID-5: nombre del afiliado. Mismo índice de campo tanto en lo que
    # mandamos (PID|||nro^barra^token^SANCOR_SALUD^HC^SANCOR_SALUD||UNKNOWN)
    # como en lo que Sancor responde (PID|1|dni|credencial||APELLIDO^NOMBRE||
    # fecha_nac|sexo): en ambos casos el campo 5 es el nombre.
    nombre_afiliado = None
    campos_pid = _campos_segmento(hl7, "PID")
    if campos_pid and len(campos_pid) >= 5:
        nombre = campos_pid[4].replace("^", " ").strip()
        if nombre and nombre.upper() not in ("UNKNOWN", "UNKNOWN UNKNOWN"):
            nombre_afiliado = nombre[:100]

    return RespuestaSancor(
        autorizada=autorizada,
        estado_detalle=descripcion[:250],
        nro_autorizacion=nro_autorizacion,
        nombre_afiliado=nombre_afiliado,
        requiere_gestion=(not autorizada and codigo_resultado in CODIGOS_PENDIENTE),
        codigo_resultado=codigo_resultado,
        crudo=hl7[:8000],
    )


# ── Transporte ────────────────────────────────────────────────────────────────

def _destino() -> tuple[str, str, str]:
    """(url, pasaporte, processing_id) según el modo configurado."""
    modo = (settings.SANCOR_MODO or MODO_SIMULADO).strip().lower()
    if modo == MODO_PRODUCCION:
        return (settings.SANCOR_URL_PROD, settings.SANCOR_PASAPORTE_PROD.get_secret_value(), "P")
    return (settings.SANCOR_URL_TEST, settings.SANCOR_PASAPORTE_TEST.get_secret_value(), "D")


def modo_actual() -> str:
    return (settings.SANCOR_MODO or MODO_SIMULADO).strip().lower()


async def _postear(url: str, cuerpo: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=settings.SANCOR_TIMEOUT, verify=False) as cli:
            r = await cli.post(url, content=cuerpo.encode("utf-8"),
                               headers={"Content-Type": "application/xml"})
            r.raise_for_status()
            # Decodificación explícita en vez de `r.text`: Sancor no siempre manda
            # un charset correcto en el header, y `r.text` adivina mal a veces
            # (mojibake tipo "inválido" → "inv�lido" en las respuestas reales
            # observadas). UTF-8 primero porque es lo que se ve más seguido en
            # producción; ISO-8859-1 nunca falla como fallback.
            try:
                return r.content.decode("utf-8")
            except UnicodeDecodeError:
                return r.content.decode("latin-1")
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


async def anular(*, nro_autorizacion: str) -> RespuestaSancor:
    """Anula una autorización previa (ZQA^Z04).

    Igual que `autorizar`, en modo simulado no sale nada. En test/producción
    esto **da de baja la autorización en Sancor**, no sólo en nuestra base.

    Devuelve la respuesta **sin interpretar el resultado de negocio**: para el
    Z04, igual que para el Z02, quien decide si salió bien es
    `ValidadorSancor.anular()` leyendo `codigo_resultado` / `autorizada`. Este
    módulo sólo transporta y parsea.

    Antes se le anteponía "ANULADA EN SANCOR · " al detalle acá mismo, sin
    mirar nada — afirmaba algo que el transporte no está en condiciones de
    saber. Con la respuesta real de Sancor test el campo quedaba
    autocontradictorio: "ANULADA EN SANCOR · No se puede anular una
    autorización facturada".
    """
    url, pasaporte, processing_id = _destino()
    modo = modo_actual()

    mensaje = construir_mensaje_anulacion(
        nro_autorizacion=nro_autorizacion,
        processing_id=processing_id,
    )

    if modo == MODO_SIMULADO:
        # `autorizada=True` porque en simulado no hay nada que anular: se
        # devuelve el final feliz para que el flujo de baja se pueda probar
        # entero sin tocar Sancor. Con `False` se marcaría cada baja simulada
        # como pendiente de anular a mano, que es justo lo contrario.
        return RespuestaSancor(
            autorizada=True,
            codigo_resultado="B000",
            estado_detalle="ANULACIÓN SIMULADA (no se consultó a Sancor)",
            modo=modo,
            enviado=mensaje,
        )

    log.info("Sancor [%s] → anulación autorización=%s", modo, nro_autorizacion)
    crudo = await _postear(url, _envolver_soap(mensaje, pasaporte))
    resultado = interpretar_respuesta(crudo)
    resultado.modo = modo
    resultado.enviado = mensaje
    if not resultado.autorizada:
        log.warning(
            "Sancor [%s] rechazó la anulación de %s: %s^%s — la autorización sigue viva allá.",
            modo, nro_autorizacion, resultado.codigo_resultado, resultado.estado_detalle,
        )
    return resultado
