"""Cliente del autorizador de Nobis Salud (O.S. 62) — WSGeCROS de Gecros.

Nobis expone un **SOAP 1.2** cuyos métodos reciben y devuelven **XML embebido
como string** dentro del parámetro/resultado. O sea: hay dos capas de XML — el
sobre SOAP y, adentro, un documento escapado que es el que trae los datos. Este
módulo se ocupa sólo de eso: armar el pedido, mandarlo y traducir la respuesta a
un resultado tipado. No sabe nada de la base de datos.

Portado de `legacy_cmc_php/src/nobis/` (adapter PHP + su README), que sigue
funcionando en paralelo y no se toca.

⚠️ SEGURIDAD OPERATIVA
──────────────────────
Insertar una autorización acá es un efecto real en el sistema de Nobis: genera
una orden con su número. Anularla también lo es. Por eso:

* `NOBIS_MODO` arranca en **"simulado"** y no sale ningún request hasta que
  alguien lo cambie a propósito;
* "test" pega contra `wstest.nobissalud.com:7004`;
* "produccion" pega contra `servicioweb.nobissalud.com.ar`.

Ojo con validar dos veces la misma prestación desde el panel y desde el PHP.

Operaciones cubiertas
─────────────────────
| Método SOAP              | Para qué                                   |
|--------------------------|--------------------------------------------|
| `ConsultarAfiliado`      | existe / está activo, nombre, plan         |
| `InsertarAutorizacionAmb`| crea la orden; devuelve estado y número    |
| `AnularOrdenNroCod`      | da de baja la orden                        |
"""
import datetime
import html
import logging
import random
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional
from xml.sax.saxutils import escape

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

MODO_SIMULADO = "simulado"
MODO_TEST = "test"
MODO_PRODUCCION = "produccion"

SOAP_NS = "http://www.w3.org/2003/05/soap-envelope"
# Namespace de los métodos del WSGeCROS.
GECROS_NS = "http://tempuri.org/"

# Ventana máxima entre prescripción y realización que acepta Nobis. Se valida
# acá para no gastar un request en algo que el WS va a rechazar igual.
MAX_DIAS_PRESCRIPCION = 60

# Estados que devuelve InsertarAutorizacionAmb en <Estado>. Vienen como
# "A-Autorizado" / "P-Pendiente" / "R-Rechazada"; se compara por la inicial
# porque el texto después del guion cambia según el caso.
ESTADO_AUTORIZADO = "A"
ESTADO_PENDIENTE = "P"
ESTADO_RECHAZADO = "R"

# ── Cómo se arma el <Item> ────────────────────────────────────────────────────
#
# Nobis homologa la práctica de su lado: acá no se traduce el código, se manda
# tal cual lo tiene el Colegio — mismo criterio del legacy. Lo único que varía
# por código es **bajo qué nomenclador hay que anunciarlo**, y Nobis acepta dos
# formas de decirlo. No está documentado en ningún lado: la regla vive
# hardcodeada en `helpers/NobisSoapLowLevel.php::buildOrdenXml()` del sistema
# viejo, que es lo que corre en producción, y está respaldada por el
# `soap.log` del servidor (341 requests con su respuesta, relevado el
# 2026-08-19 y re-cruzado por código el 2026-08-30).
#
#   forma DIRECTA  → <TipoNomenclador>1</TipoNomenclador>
#                    <CodPractica>420101</CodPractica>
#                    (sin OrigenTipoNomCod ni OrigenPracticaCod)
#
#   forma ORIGEN   → <TipoNomenclador></TipoNomenclador><CodPractica></CodPractica>
#                    <OrigenTipoNomCod>99</OrigenTipoNomCod>
#                    <OrigenPracticaCod>420351</OrigenPracticaCod>
#
# La primera pasada (2026-08-19) miró el estado agregado por forma y encontró
# que `99` gana lejos. Es cierto, pero mezclaba códigos: agrupar por forma sin
# mirar el código escondía que la regla real es **por código**, no global. La
# segunda pasada (2026-08-30) filtró por el mensaje que de verdad distingue
# "forma equivocada" de "rechazo de negocio" — `Nomenclador o Practica <código>
# Inexistente`, que Nobis sólo devuelve cuando busca el código en el
# nomenclador que no es — y cruzó ese mensaje contra la forma enviada, código
# por código:
#
#   | código   | falla con...        | nunca fallo así con...      |
#   |----------|----------------------|------------------------------|
#   | 420101   | ORIGEN 99 (1 vez)    | DIRECTA (9 A / 3 P / 2 R)    |
#   | 420112   | ORIGEN 99 (5 veces)  | DIRECTA/ORIGEN 1 (siempre    |
#   |          |                      | "sin valor en convenio", un  |
#   |          |                      | rechazo de negocio, no de    |
#   |          |                      | nomenclador)                 |
#   | 420130   | ORIGEN 1 (1 vez)     | ORIGEN 99 (autoriza)         |
#   | 420132   | DIRECTA y ORIGEN 1   | ORIGEN 99 (autoriza)         |
#   | 420351   | DIRECTA y ORIGEN 1   | ORIGEN 99 (autoriza)         |
#
# O sea: `420101` y `420112` viven bajo el nomenclador `1` — hay que
# anunciarlos en forma DIRECTA (o, es lo mismo para Nobis, `OrigenTipoNomCod=1`;
# la forma DIRECTA es la que usa el PHP real). Todo el resto vive bajo `99`.
# **`420112` faltaba en `CODIGOS_FORMA_DIRECTA`** — con `99` volvía "Nomenclador
# o Practica 420112 Inexistente" las 5 veces que se probó; no autorizaba nunca
# ahí, y encima hoy tampoco tiene precio cargado en `nm_valores` para la O.S. 62
# (ver auditoría del 2026-08-30), así que la carga real de este código estaba
# doblemente rota.
ORIGEN_TIPO_NOM_COD = "99"

# `TipoNomenclador` de la forma directa. Es el valor de todas las órdenes reales.
TIPO_NOMENCLADOR_DIRECTO = "1"

# Códigos que van en la forma DIRECTA porque Nobis los tiene en el nomenclador
# `1`, no en el `99`. Se deja como conjunto editable a mano —mismo criterio que
# el homologador de Sancor— porque es una tabla de convenio que el Colegio va a
# ir ampliando a medida que aparezcan casos: con `99` estos dos códigos vuelven
# "Inexistente"; con la forma directa, Nobis los reconoce (después puede
# rechazarlos por otro motivo de negocio, pero ya no por el código).
CODIGOS_FORMA_DIRECTA = frozenset({"420101", "420112"})


@dataclass
class RespuestaNobis:
    """Resultado normalizado de una operación contra el WSGeCROS."""

    autorizada: bool
    # Texto tal cual lo devolvió Nobis (o el motivo del rechazo).
    estado_detalle: str
    # Letra del estado: A / P / R. `None` si no vino o no se entendió.
    estado: Optional[str] = None
    # `Num` de la respuesta: el número de orden. Es lo que hace falta para anular.
    nro_orden: Optional[str] = None
    # `Cod` de la respuesta: el código de autorización.
    cod_autorizacion: Optional[str] = None
    nombre_afiliado: Optional[str] = None
    # Coseguro que informa Nobis (Cose_Total). Lo paga el afiliado.
    coseguro: Optional[str] = None
    # `True` cuando quedó pendiente de gestión en la obra social.
    requiere_gestion: bool = False
    # XML crudo, para soporte. No se expone al prestador.
    crudo: str = ""
    modo: str = MODO_SIMULADO
    enviado: str = field(default="", repr=False)


class NobisError(Exception):
    """Falla de transporte o respuesta ininteligible del WSGeCROS."""


# ── Armado de los XML embebidos ───────────────────────────────────────────────

def _fecha_dmy(fecha: datetime.date) -> str:
    """Nobis espera dd/mm/YYYY en todas las fechas."""
    return fecha.strftime("%d/%m/%Y")


def construir_xml_afiliado(
    *,
    numero_afiliado: str = "",
    tipo_doc: str = "",
    nro_doc: str = "",
    fecha: Optional[datetime.date] = None,
) -> str:
    """`<Afiliado>` de ConsultarAfiliado.

    Se puede consultar por número de afiliado o por (tipo_doc, nro_doc). Los
    tags van igual aunque vayan vacíos: en algunos ambientes el WS es sensible a
    que falten (ver las notas del README legacy).
    """
    fecha = fecha or datetime.date.today()
    return (
        "<Afiliado>"
        f"<TipoDoc>{escape(tipo_doc)}</TipoDoc>"
        f"<NroDoc>{escape(nro_doc)}</NroDoc>"
        f"<NumeroAfiliado>{escape(numero_afiliado)}</NumeroAfiliado>"
        f"<Fecha>{escape(_fecha_dmy(fecha))}</Fecha>"
        "</Afiliado>"
    )


def _construir_item(
    *, codigo_practica: str, cantidad: int, mat_prov: str, tipo_solic: str
) -> str:
    """Un `<Item>` de la orden, en la forma que Nobis acepta para ese código.

    `MatEfector`/`TipoEfector` van iguales al solicitante en las dos formas: por
    convenio el efector es siempre el mismo médico.
    """
    comunes = (
        f"<Cantidad>{escape(str(cantidad))}</Cantidad>"
        f"<MatEfector>{escape(mat_prov)}</MatEfector>"
        f"<TipoEfector>{escape(tipo_solic)}</TipoEfector>"
    )

    if codigo_practica in CODIGOS_FORMA_DIRECTA:
        return (
            "<Item>"
            f"<TipoNomenclador>{TIPO_NOMENCLADOR_DIRECTO}</TipoNomenclador>"
            f"<CodPractica>{escape(codigo_practica)}</CodPractica>"
            f"{comunes}"
            "</Item>"
        )

    return (
        "<Item>"
        "<TipoNomenclador></TipoNomenclador>"
        "<CodPractica></CodPractica>"
        f"{comunes}"
        f"<OrigenTipoNomCod>{ORIGEN_TIPO_NOM_COD}</OrigenTipoNomCod>"
        f"<OrigenPracticaCod>{escape(codigo_practica)}</OrigenPracticaCod>"
        "</Item>"
    )


def construir_xml_orden(
    *,
    numero_afiliado: str,
    mat_prov: str,
    tipo_solic: str,
    cod_entidad_efectora: str,
    codigo_practica: str,
    token: str = "",
    cantidad: int = 1,
    fecha_prescripcion: Optional[datetime.date] = None,
    fecha_realizacion: Optional[datetime.date] = None,
    diagnostico: str = "",
    codigo_cie10: str = "",
) -> str:
    """`<Orden>` de InsertarAutorizacionAmb.

    Reglas del convenio, replicadas del legacy:

    * `MatEfector` == `MatProv` y `TipoEfector` == `TipoSolic`: el solicitante y
      el efector son **siempre el mismo médico**.
    * `CodEntidadEfectora` es fijo: la entidad es el Colegio.

    El `<Item>` se arma en una de dos formas según el código — ver
    `CODIGOS_FORMA_DIRECTA` y `ORIGEN_TIPO_NOM_COD` arriba, que es donde está
    la evidencia. Es la misma regla que aplica el PHP que corre en producción.

    ## El `<Token>` sí se manda

    Durante un tiempo este módulo no lo mandaba, con la explicación de que "el
    WSGeCROS no tiene dónde recibirlo". Es falso: el campo está documentado por
    Gecros ("opcional dependiendo la configuración que tenga la entidad
    efectora"), está en el builder del PHP que corre en producción, y **las 130
    órdenes del `soap.log` del servidor lo llevan — ninguna va sin él**. Nobis
    lo valida: 21 respuestas reales contestan "Token incorrecto".

    Va entre `<Diagnostico>` y `<Items>`, que es la posición del builder real.
    Los tokens de Nobis son de 6 dígitos (`290894`, `666766`), no de 4.
    """
    hoy = datetime.date.today()
    fecha_prescripcion = fecha_prescripcion or hoy
    fecha_realizacion = fecha_realizacion or hoy

    item = _construir_item(
        codigo_practica=codigo_practica,
        cantidad=cantidad,
        mat_prov=mat_prov,
        tipo_solic=tipo_solic,
    )

    return (
        "<Orden>"
        "<NroOrden></NroOrden>"
        f"<NumeroAfiliado>{escape(numero_afiliado)}</NumeroAfiliado>"
        "<TipoDoc></TipoDoc>"
        "<NroDoc></NroDoc>"
        f"<MatProv>{escape(mat_prov)}</MatProv>"
        f"<TipoSolic>{escape(tipo_solic)}</TipoSolic>"
        "<CodAutoriz></CodAutoriz>"
        f"<FechaPrescripcion>{escape(_fecha_dmy(fecha_prescripcion))}</FechaPrescripcion>"
        f"<CodEntidadEfectora>{escape(cod_entidad_efectora)}</CodEntidadEfectora>"
        f"<CodigoCIE10>{escape(codigo_cie10)}</CodigoCIE10>"
        f"<FechaRealizacion>{escape(_fecha_dmy(fecha_realizacion))}</FechaRealizacion>"
        f"<Diagnostico>{escape(diagnostico)}</Diagnostico>"
        f"<Token>{escape(token)}</Token>"
        f"<Items>{item}</Items>"
        "</Orden>"
    )


# Orden en que cada operación declara sus parámetros en el WSDL. **No es una
# preferencia de estilo**: el WSGeCROS los declara dentro de un `<s:sequence>`,
# y en SOAP document/literal la secuencia es parte del contrato — un elemento
# fuera de lugar no se liga al parámetro que corresponde.
#
# Está acá y no en cada llamador porque el bug ya pasó una vez: `anular_orden()`
# armaba el diccionario con `pNroOrden` al final (se agregaba condicionalmente)
# cuando el WSDL lo declara tercero. Con el orden centralizado, un llamador no
# puede equivocarse aunque arme el dict como se le ocurra.
#
# Relevado del WSDL vivo de test y de producción el 2026-08-19 (los dos declaran
# lo mismo).
ORDEN_PARAMETROS: dict[str, tuple[str, ...]] = {
    "ConsultarAfiliado": ("pUsuario", "pClave", "pXml"),
    "InsertarAutorizacionAmb": ("pUsuario", "pClave", "pXml"),
    "AnularOrdenNroCod": ("pUsuario", "pClave", "pNroOrden", "pCodAut", "pObservaciones"),
    "ConsultarOrdenCompleta": ("pUsuario", "pClave", "pNroOrden"),
}


def _envolver_soap(metodo: str, parametros: dict[str, str]) -> str:
    """Sobre SOAP 1.2 con el método y sus parámetros como hijos.

    Los emite en el orden que declara el WSDL (`ORDEN_PARAMETROS`), no en el que
    vinieron en el diccionario. Un parámetro que no esté en el mapa es un error
    de programación —un typo en el nombre llega al WS como `null` y no da error
    de transporte, así que se corta acá— y uno declarado que no se pasó
    simplemente se omite: todos son `minOccurs="0"`.
    """
    declarados = ORDEN_PARAMETROS.get(metodo)
    if declarados is None:
        raise NobisError(f"Operación de Nobis sin orden de parámetros declarado: {metodo!r}.")

    desconocidos = set(parametros) - set(declarados)
    if desconocidos:
        raise NobisError(
            f"{metodo} no declara {sorted(desconocidos)} en el WSDL. "
            f"Los que acepta son: {list(declarados)}."
        )

    cuerpo = "".join(
        f"<{nombre}>{escape(parametros[nombre])}</{nombre}>"
        for nombre in declarados
        if nombre in parametros
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<soap12:Envelope xmlns:soap12="{SOAP_NS}">'
        "<soap12:Body>"
        f'<{metodo} xmlns="{GECROS_NS}">{cuerpo}</{metodo}>'
        "</soap12:Body>"
        "</soap12:Envelope>"
    )


# ── Lectura de las respuestas ─────────────────────────────────────────────────

def extraer_xml_embebido(respuesta_soap: str) -> Optional[str]:
    """Saca el documento XML que viene como texto dentro del sobre SOAP.

    El WS devuelve `<XxxResult>&lt;DocumentElement&gt;…</XxxResult>`: el
    contenido está escapado, así que hay que desescaparlo antes de parsearlo.
    """
    if not respuesta_soap:
        return None

    m = re.search(r"<\w*:?\w*Result>(.*?)</\w*:?\w*Result>", respuesta_soap, re.DOTALL)
    crudo = m.group(1) if m else respuesta_soap

    # Puede venir escapado (&lt;) o no, según el ambiente.
    if "&lt;" in crudo:
        crudo = html.unescape(crudo)

    crudo = crudo.strip()
    return crudo or None


def _texto(nodo: Optional[ET.Element]) -> Optional[str]:
    if nodo is None or nodo.text is None:
        return None
    return nodo.text.strip() or None


def _buscar(raiz: ET.Element, tag: str) -> Optional[ET.Element]:
    """Primer nodo con ese tag, sin importar a qué profundidad esté.

    El WS a veces envuelve en `<DocumentElement>` y a veces no, así que buscar
    por tag es más robusto que asumir la forma del árbol.
    """
    if raiz.tag == tag:
        return raiz
    return raiz.find(f".//{tag}")


def interpretar_afiliado(xml_texto: str) -> RespuestaNobis:
    """Traduce la respuesta de ConsultarAfiliado."""
    try:
        raiz = ET.fromstring(xml_texto)
    except ET.ParseError as e:
        raise NobisError(f"Nobis devolvió un XML que no se pudo leer: {e}") from e

    nombre = None
    for tag in ("Afiliado", "ApellidoNombre", "Nombre"):
        nombre = _texto(_buscar(raiz, tag))
        if nombre:
            break

    estado = None
    for tag in ("Estado", "Mensaje"):
        estado = _texto(_buscar(raiz, tag))
        if estado:
            break

    # Mismo criterio que el legacy: "ACTIVO" o "CON COBERTURA", cuidando de no
    # dar por activo un "INACTIVO" (que también contiene "ACTIV").
    up = (estado or "").upper()
    activo = bool(up) and (
        ("ACTIV" in up and "INACTIV" not in up) or "CON COBERTURA" in up
    )

    return RespuestaNobis(
        autorizada=activo,
        estado_detalle=estado or "Nobis no informó el estado del afiliado.",
        nombre_afiliado=nombre,
        crudo=xml_texto[:8000],
    )


def interpretar_autorizacion(xml_texto: str) -> RespuestaNobis:
    """Traduce la respuesta de InsertarAutorizacionAmb.

    El nodo `<Autorizacion>` trae `Mensaje`, `Estado` (`A-…`/`P-…`/`R-…`), `Cod`
    (código de autorización), `Num` (número de orden) y el coseguro.
    """
    try:
        raiz = ET.fromstring(xml_texto)
    except ET.ParseError as e:
        raise NobisError(f"Nobis devolvió un XML que no se pudo leer: {e}") from e

    mensaje = _texto(_buscar(raiz, "Mensaje"))
    if mensaje:
        # A veces el mensaje trae <br> literales.
        mensaje = re.sub(r"<br\s*/?>", "\n", mensaje).strip()

    estado_txt = _texto(_buscar(raiz, "Estado"))
    letra = (estado_txt or "").strip()[:1].upper() or None

    autorizada = letra == ESTADO_AUTORIZADO
    pendiente = letra == ESTADO_PENDIENTE

    detalle = estado_txt or mensaje or "Nobis no informó el estado de la orden."
    if mensaje and estado_txt and mensaje not in estado_txt:
        detalle = f"{estado_txt} · {mensaje}"

    return RespuestaNobis(
        autorizada=autorizada,
        estado=letra,
        estado_detalle=detalle,
        nro_orden=_texto(_buscar(raiz, "Num")),
        cod_autorizacion=_texto(_buscar(raiz, "Cod")),
        coseguro=_texto(_buscar(raiz, "Cose_Total")),
        requiere_gestion=pendiente,
        crudo=xml_texto[:8000],
    )


def interpretar_anulacion(xml_texto: str) -> RespuestaNobis:
    """Traduce la respuesta de AnularOrdenNroCod (`Mensaje` + `Estado` OK/ERROR)."""
    try:
        raiz = ET.fromstring(xml_texto)
    except ET.ParseError as e:
        raise NobisError(f"Nobis devolvió un XML que no se pudo leer: {e}") from e

    mensaje = _texto(_buscar(raiz, "Mensaje")) or ""
    estado = _texto(_buscar(raiz, "Estado")) or ""
    ok = estado.upper().startswith("OK") or "ANULADA" in mensaje.upper()

    return RespuestaNobis(
        autorizada=False,
        estado_detalle=(mensaje or estado or "Nobis no informó el resultado.").strip(),
        estado="OK" if ok else "ERROR",
        crudo=xml_texto[:8000],
    )


# ── Transporte ────────────────────────────────────────────────────────────────

def modo_actual() -> str:
    return (settings.NOBIS_MODO or MODO_SIMULADO).strip().lower()


def _destino() -> str:
    """URL del endpoint según el modo configurado."""
    if modo_actual() == MODO_PRODUCCION:
        return settings.NOBIS_URL_PROD
    return settings.NOBIS_URL_TEST


async def _postear(metodo: str, parametros: dict[str, str]) -> str:
    """Manda el sobre y devuelve el XML embebido de la respuesta."""
    url = _destino()
    cuerpo = _envolver_soap(metodo, parametros)
    try:
        # Verificación TLS default (sin `verify=False`): los certificados de
        # wstest.nobissalud.com:7004 y servicioweb.nobissalud.com.ar validan
        # sin problema contra la cadena estándar — probado el 2026-08-30.
        async with httpx.AsyncClient(timeout=settings.NOBIS_TIMEOUT) as cli:
            r = await cli.post(
                url,
                content=cuerpo.encode("utf-8"),
                headers={
                    "Content-Type": "application/soap+xml; charset=utf-8",
                    "SOAPAction": f"{GECROS_NS}{metodo}",
                },
            )
            r.raise_for_status()
            texto = r.text
    except httpx.TimeoutException as e:
        raise NobisError("Nobis no respondió a tiempo. Reintentá en unos minutos.") from e
    except httpx.HTTPError as e:
        raise NobisError(f"No se pudo contactar al servicio de Nobis: {e}") from e

    embebido = extraer_xml_embebido(texto)
    if not embebido:
        raise NobisError("Nobis respondió sin contenido interpretable.")
    return embebido


def _credenciales() -> dict[str, str]:
    return {"pUsuario": settings.NOBIS_USUARIO, "pClave": settings.NOBIS_CLAVE.get_secret_value()}


async def consultar_afiliado(
    *,
    numero_afiliado: str = "",
    tipo_doc: str = "",
    nro_doc: str = "",
    fecha: Optional[datetime.date] = None,
) -> RespuestaNobis:
    """Consulta si el afiliado existe y está activo. En simulado no sale nada."""
    xml = construir_xml_afiliado(
        numero_afiliado=numero_afiliado, tipo_doc=tipo_doc, nro_doc=nro_doc, fecha=fecha
    )
    modo = modo_actual()

    if modo == MODO_SIMULADO:
        return RespuestaNobis(
            autorizada=True,
            estado_detalle="ACTIVO (simulado — no se consultó a Nobis)",
            nombre_afiliado="AFILIADO SIMULADO",
            modo=modo,
            enviado=xml,
        )

    log.info("Nobis [%s] → ConsultarAfiliado afiliado=%s", modo, numero_afiliado or nro_doc)
    embebido = await _postear("ConsultarAfiliado", {**_credenciales(), "pXml": xml})
    r = interpretar_afiliado(embebido)
    r.modo = modo
    r.enviado = xml
    return r


async def insertar_autorizacion(
    *,
    numero_afiliado: str,
    mat_prov: str,
    codigo_practica: str,
    token: str = "",
    cantidad: int = 1,
    fecha_prescripcion: Optional[datetime.date] = None,
    fecha_realizacion: Optional[datetime.date] = None,
) -> RespuestaNobis:
    """Crea la orden en Nobis. En modo simulado no sale ningún request.

    En test/producción esto **genera una orden real** con su número.
    """
    hoy = datetime.date.today()
    fecha_prescripcion = fecha_prescripcion or hoy
    fecha_realizacion = fecha_realizacion or hoy

    if fecha_realizacion < fecha_prescripcion:
        raise NobisError("La fecha de realización no puede ser anterior a la de prescripción.")
    if (fecha_realizacion - fecha_prescripcion).days > MAX_DIAS_PRESCRIPCION:
        raise NobisError(
            f"La fecha de realización supera los {MAX_DIAS_PRESCRIPCION} días "
            "desde la prescripción."
        )

    xml = construir_xml_orden(
        numero_afiliado=numero_afiliado,
        mat_prov=mat_prov,
        tipo_solic=settings.NOBIS_TIPO_SOLIC,
        cod_entidad_efectora=settings.NOBIS_COD_ENTIDAD_EFECTORA,
        codigo_practica=codigo_practica,
        token=token,
        cantidad=cantidad,
        fecha_prescripcion=fecha_prescripcion,
        fecha_realizacion=fecha_realizacion,
    )
    modo = modo_actual()

    if modo == MODO_SIMULADO:
        # Orden armada localmente: sirve para probar la pantalla completa sin
        # tocar Nobis. El número es obviamente falso.
        return RespuestaNobis(
            autorizada=True,
            estado="A",
            estado_detalle="A-Autorizado (simulado — no se consultó a Nobis)",
            nro_orden=f"SIM{random.randint(100000, 999999)}",
            cod_autorizacion=f"SIM{random.randint(1000, 9999)}",
            nombre_afiliado="AFILIADO SIMULADO",
            modo=modo,
            enviado=xml,
        )

    log.info(
        "Nobis [%s] → InsertarAutorizacionAmb código=%s afiliado=%s matrícula=%s",
        modo, codigo_practica, numero_afiliado, mat_prov,
    )
    # `pXml`, no `pXmlOrden`. Lo declara así el WSDL —vivo, verificado el
    # 2026-08-19— y es lo que manda el PHP en los 331 requests del `soap.log`
    # de producción, donde `pXmlOrden` no aparece ni una vez.
    #
    # El error venía de leer mal el adapter PHP: elige el nombre con un
    # condicional (`pXmlOrden` sólo si la operación contiene "Prestacion" u
    # "Orden") y `InsertarAutorizacionAmb` no contiene ninguno de los dos. Con
    # el nombre equivocado el `.asmx` no falla: recibe la orden como `null`.
    embebido = await _postear(
        "InsertarAutorizacionAmb", {**_credenciales(), "pXml": xml}
    )
    r = interpretar_autorizacion(embebido)
    r.modo = modo
    r.enviado = xml
    return r


async def anular_orden(
    *, cod_autorizacion: str, nro_orden: str = "", observaciones: str = "ANULADA DESDE EL PANEL"
) -> RespuestaNobis:
    """Da de baja la orden en Nobis.

    `cod_autorizacion` (pCodAut) es **obligatorio**: sin él el WS no anula (está
    remarcado en el README del legacy). En modo simulado no sale nada; en
    test/producción la baja es real.

    El orden en que salen los parámetros no lo decide este diccionario sino
    `ORDEN_PARAMETROS`: el WSDL declara `pNroOrden` **antes** que `pCodAut`, y
    acá se armaba al revés porque el número de orden se agregaba al final de
    forma condicional.
    """
    if not (cod_autorizacion or "").strip():
        raise NobisError("Nobis necesita el código de autorización para anular la orden.")

    modo = modo_actual()
    if modo == MODO_SIMULADO:
        return RespuestaNobis(
            autorizada=False,
            estado="OK",
            estado_detalle="ANULACIÓN SIMULADA (no se consultó a Nobis)",
            modo=modo,
        )

    params = {
        **_credenciales(),
        "pCodAut": cod_autorizacion.strip(),
        "pObservaciones": (observaciones or "").strip() or "ANULADA DESDE EL PANEL",
    }
    if (nro_orden or "").strip():
        params["pNroOrden"] = nro_orden.strip()

    log.info("Nobis [%s] → AnularOrdenNroCod cod_aut=%s orden=%s", modo, cod_autorizacion, nro_orden)
    embebido = await _postear("AnularOrdenNroCod", params)
    r = interpretar_anulacion(embebido)
    r.modo = modo
    return r
