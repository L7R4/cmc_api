"""Cliente del validador de OSPJN — Obra Social del Poder Judicial (O.S. 151).

OSPJN expone un **REST con JSON** (WCF `PrestadorService.svc`). Son dos pasos:

1. `POST /Ingresar` con el usuario de API del Colegio → devuelve un `Token`.
2. `POST /ValidarAfiliado` con ese token en `Authorization: Bearer` → dice si el
   afiliado está en condiciones y devuelve el `NroConsulta`.

Este módulo se ocupa sólo de eso. No sabe nada de la base de datos.

Portado de `legacy_cmc_php/src/judicial/`, que sigue funcionando en paralelo.

⚠️ SEGURIDAD OPERATIVA
──────────────────────
`OSPJN_MODO` arranca en **"simulado"** y no sale ningún request hasta que
alguien lo cambie a propósito. A diferencia de Sancor y Nobis, acá la operación
es una *validación* y no crea una orden que después haya que anular — igual se
mantiene el modo simulado por consistencia y para no gastar consultas reales
mientras se prueba la pantalla.

Diferencias con el legacy, a propósito
──────────────────────────────────────
* El PHP arma el JSON a mano y le queda una **coma final** (`"CodigoPrestacion":
  "X",}`), que es JSON inválido. Acá se serializa con `json.dumps`.
* El PHP pide un token nuevo en cada carga de pantalla y lo pasea por el
  formulario en un `<input hidden>`. Acá el token no sale nunca del backend.
"""
import datetime
import json
import logging
import random
from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

MODO_SIMULADO = "simulado"
MODO_TEST = "test"
MODO_PRODUCCION = "produccion"

# `CodigoPrestacion` no es el código del Colegio: es una **categoría de tres
# letras** que OSPJN usa para saber qué se está validando. En el nomenclador
# legacy (`nomenclador.CODIGOJUDICIALES`) sólo existen dos valores: 'CON' para
# las consultas (los 42*, más 430202) y 'OTR' para todo el resto.
CATEGORIA_CONSULTA = "CON"
CATEGORIA_OTRAS = "OTR"

# Los códigos de consulta: el rango 42* completo más el 430202, que el legacy
# clasificaba a mano.
_PREFIJO_CONSULTA = "42"
_CONSULTAS_EXTRA = frozenset({"430202"})


def categoria_de_codigo(codigo: str) -> str:
    """Categoría que espera OSPJN en `CodigoPrestacion`, derivada del código.

    Se deriva y NO se guarda: la regla es una función del número (42* + 430202),
    así que una columna en `nm_nomenclador` sería un dato duplicado que puede
    quedar desincronizado — y encima una columna de UNA obra social dentro de la
    tabla compartida por todas.
    """
    limpio = (codigo or "").strip()
    if limpio.startswith(_PREFIJO_CONSULTA) or limpio in _CONSULTAS_EXTRA:
        return CATEGORIA_CONSULTA
    return CATEGORIA_OTRAS

# Respuestas conocidas que NO son una validación válida. El WS las manda en
# `Mensaje` o en `EstadoDescripcion` (ver validarjudial_1.php).
MENSAJE_SIN_TOKEN = "No se encuentra token de autenticación"
MENSAJE_NO_ENCONTRADO = "Afiliado NO encontrado"
ESTADOS_NO_VALIDOS = ("INACTIVO", "SUSPENDIDO")


@dataclass
class RespuestaOspjn:
    """Resultado normalizado de una validación."""

    validado: bool
    # Texto tal cual lo devolvió OSPJN (o el motivo del rechazo).
    estado_detalle: str
    # `NroConsulta`: el número que acredita la validación. 0/None si no validó.
    nro_consulta: Optional[str] = None
    nombre_afiliado: Optional[str] = None
    nro_afiliado: Optional[str] = None
    nro_documento: Optional[str] = None
    # `EstadoDescripcion` crudo: ACTIVO / INACTIVO / SUSPENDIDO / …
    estado: Optional[str] = None
    # JSON crudo, para soporte. No se expone al prestador.
    crudo: str = ""
    modo: str = MODO_SIMULADO
    enviado: str = field(default="", repr=False)


class OspjnError(Exception):
    """Falla de transporte, de autenticación o respuesta ininteligible."""


# ── Transporte ────────────────────────────────────────────────────────────────

def modo_actual() -> str:
    return (settings.OSPJN_MODO or MODO_SIMULADO).strip().lower()


def _base_url() -> str:
    if modo_actual() == MODO_PRODUCCION:
        return settings.OSPJN_URL_PROD.rstrip("/")
    return settings.OSPJN_URL_TEST.rstrip("/")


async def _postear(cli: httpx.AsyncClient, ruta: str, cuerpo: dict, token: str = "") -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"{_base_url()}/{ruta}"
    try:
        r = await cli.post(url, content=json.dumps(cuerpo).encode("utf-8"), headers=headers)
        r.raise_for_status()
    except httpx.TimeoutException as e:
        raise OspjnError("OSPJN no respondió a tiempo. Reintentá en unos minutos.") from e
    except httpx.HTTPError as e:
        raise OspjnError(f"No se pudo contactar al servicio de OSPJN: {e}") from e

    try:
        datos = r.json()
    except ValueError as e:
        raise OspjnError("OSPJN devolvió una respuesta que no es JSON.") from e

    if not isinstance(datos, dict):
        raise OspjnError("OSPJN devolvió una respuesta con una forma inesperada.")
    return datos


async def _ingresar(cli: httpx.AsyncClient) -> str:
    """Obtiene el token de sesión con el usuario de API del Colegio.

    El token es del **Colegio**, no del prestador ni del afiliado: las
    credenciales son institucionales y nunca salen del backend.

    Se pide uno nuevo por validación, igual que hace el legacy en cada carga de
    pantalla. No se cachea porque el servicio no documenta la vigencia del
    token, y un token vencido reusado fallaría en silencio con
    "No se encuentra token de autenticación".
    """
    datos = await _postear(
        cli,
        "Ingresar",
        {"Username": settings.OSPJN_USUARIO, "Password": settings.OSPJN_PASSWORD},
    )
    token = (datos.get("Token") or "").strip()
    if not token:
        mensaje = (datos.get("Mensaje") or "").strip()
        raise OspjnError(
            f"OSPJN no devolvió token de acceso{f': {mensaje}' if mensaje else '.'}"
        )
    return token


def interpretar(datos: dict) -> RespuestaOspjn:
    """Traduce la respuesta de ValidarAfiliado.

    Criterio de validez: `NroConsulta` con un valor distinto de 0. Es el mismo
    invariante que el legacy sostiene a mano —pone `NroConsulta = 0` en cada
    caso de falla— pero acá se lee del dato en vez de reconstruirlo comparando
    strings.
    """
    estado = (datos.get("EstadoDescripcion") or "").strip() or None
    mensaje = (datos.get("Mensaje") or "").strip() or None
    nro_consulta = str(datos.get("NroConsulta") or "").strip()

    valido = bool(nro_consulta) and nro_consulta not in ("0", "-")

    # Motivos conocidos, para que el prestador lea algo entendible.
    if mensaje == MENSAJE_SIN_TOKEN:
        valido = False
        detalle = "OSPJN rechazó la sesión del Colegio (token de autenticación)."
    elif mensaje == MENSAJE_NO_ENCONTRADO:
        valido = False
        detalle = "Afiliado no encontrado en OSPJN."
    elif (estado or "").upper() in ESTADOS_NO_VALIDOS:
        valido = False
        detalle = f"El afiliado figura {estado} en OSPJN."
    else:
        detalle = " · ".join(p for p in (estado, mensaje) if p) or (
            "Validado" if valido else "OSPJN no informó el motivo."
        )

    return RespuestaOspjn(
        validado=valido,
        estado_detalle=detalle,
        estado=estado,
        nro_consulta=nro_consulta if valido else None,
        nombre_afiliado=(datos.get("Nombre") or "").strip() or None,
        nro_afiliado=(datos.get("NroAfiliado") or "").strip() or None,
        nro_documento=str(datos.get("NroDocumento") or "").strip() or None,
        crudo=json.dumps(datos, ensure_ascii=False)[:8000],
    )


async def validar_afiliado(
    *,
    numero_afiliado: str,
    barra_afiliado: str,
    categoria_prestacion: str,
    fecha: Optional[datetime.date] = None,
) -> RespuestaOspjn:
    """Valida al afiliado para una categoría de prestación.

    `categoria_prestacion` es 'CON' u 'OTR' — no el código del Colegio.
    En modo `simulado` no sale ningún request.
    """
    cuerpo = {
        "NumeroAfiliado": numero_afiliado,
        "BarraAfiliado": barra_afiliado,
        "CodigoPrestacion": categoria_prestacion,
    }
    enviado = json.dumps(cuerpo, ensure_ascii=False)
    modo = modo_actual()

    if modo == MODO_SIMULADO:
        return RespuestaOspjn(
            validado=True,
            estado_detalle="ACTIVO (simulado — no se consultó a OSPJN)",
            estado="ACTIVO",
            nro_consulta=f"SIM{random.randint(100000, 999999)}",
            nombre_afiliado="AFILIADO SIMULADO",
            nro_afiliado=f"{numero_afiliado}/{barra_afiliado}",
            modo=modo,
            enviado=enviado,
        )

    log.info(
        "OSPJN [%s] → ValidarAfiliado afiliado=%s/%s categoría=%s",
        modo, numero_afiliado, barra_afiliado, categoria_prestacion,
    )
    async with httpx.AsyncClient(timeout=settings.OSPJN_TIMEOUT, verify=False) as cli:
        token = await _ingresar(cli)
        datos = await _postear(cli, "ValidarAfiliado", cuerpo, token=token)

    res = interpretar(datos)
    res.modo = modo
    res.enviado = enviado
    return res
