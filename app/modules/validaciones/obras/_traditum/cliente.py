"""Transporte del canal Traditum: sesión, token y envío de mensajes.

No sabe de obras sociales ni de la base. Recibe un mensaje ya armado, lo
manda y devuelve la respuesta cruda; interpretarla es problema de quien lo usa.

⚠️ SEGURIDAD OPERATIVA
──────────────────────
Igual que Sancor: una autorización acá es un efecto real en el sistema del
financiador. Por eso `TRADITUM_MODO` arranca en **"simulado"** y no sale ningún
request hasta que alguien lo cambie a propósito. El modo simulado vive en el
validador de cada obra social, que es quien sabe qué respuesta tiene sentido
fabricar; este módulo sólo se niega a salir a la red.

El entorno lo decide la URL, no el mensaje: a diferencia de Sancor —donde el
processing-ID del MSH marca test o producción— acá `MSH-11` va siempre en `P` y
lo que cambia es contra qué host se postea.
"""
import asyncio
import logging
import time
from typing import Any

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

MODO_SIMULADO = "simulado"
MODO_TEST = "test"
MODO_PRODUCCION = "produccion"

# Tipos de mensaje que acepta `/api/EnviarFlat` (Anexo I del documento técnico).
TIPO_HL7_24 = "SI"
TIPO_HL7_26 = "HB"
TIPO_TEXTO_XML = "AS"

# El token vence a los 300 s en producción. Se renueva con margen para que no
# caduque en vuelo: un 401 a mitad de una autorización deja la transacción en un
# estado que no sabemos leer.
_MARGEN_RENOVACION_S = 30
_VIDA_MINIMA_S = 30

_token: str | None = None
_vence_en: float = 0.0
_candado = asyncio.Lock()


class TraditumError(Exception):
    """Falla de transporte o respuesta ininteligible del canal."""


def modo_actual() -> str:
    return (settings.TRADITUM_MODO or MODO_SIMULADO).strip().lower()


def sale_a_la_red() -> bool:
    return modo_actual() in (MODO_TEST, MODO_PRODUCCION)


def base_url() -> str:
    """Host según el modo. El de testing sólo responde de lunes a viernes de 8
    a 20 (documento técnico, pág. 2)."""
    return (
        settings.TRADITUM_URL_PROD
        if modo_actual() == MODO_PRODUCCION
        else settings.TRADITUM_URL_TEST
    )


def _reiniciar_sesion() -> None:
    """Olvida el token cacheado. Lo usa el reintento tras un 401."""
    global _token, _vence_en
    _token = None
    _vence_en = 0.0


async def _obtener_token() -> str:
    """Token de acceso, cacheado hasta poco antes de que venza.

    El candado evita que N pedidos simultáneos abran N sesiones: el primero
    hace el login y el resto reusa lo que dejó.
    """
    global _token, _vence_en

    async with _candado:
        if _token and time.monotonic() < _vence_en:
            return _token

        url = f"{base_url()}/api/login"
        try:
            async with httpx.AsyncClient(timeout=settings.TRADITUM_TIMEOUT) as cli:
                # Basic Auth en la cabecera y cuerpo vacío: el login no lleva payload.
                r = await cli.post(
                    url,
                    auth=(
                        settings.TRADITUM_USUARIO,
                        settings.TRADITUM_PASSWORD.get_secret_value(),
                    ),
                    content=b"",
                )
                r.raise_for_status()
                datos: dict[str, Any] = r.json()
        except httpx.TimeoutException as e:
            raise TraditumError(
                "Traditum no respondió a tiempo al iniciar sesión. Reintentá en unos minutos."
            ) from e
        except httpx.HTTPError as e:
            log.error("Traditum: falla de login contra %s — %r", url, e)
            raise TraditumError(
                "No pudimos autenticarnos con Traditum. Reintentá en unos minutos; "
                "si sigue igual, avisá al Colegio."
            ) from e
        except ValueError as e:  # json() sobre una respuesta que no es JSON
            raise TraditumError("Traditum devolvió un login ininteligible.") from e

        token = datos.get("access_token")
        if not token:
            raise TraditumError("Traditum no devolvió un token de acceso.")

        vida = int(datos.get("expires_in") or 300)
        _token = token
        _vence_en = time.monotonic() + max(vida - _MARGEN_RENOVACION_S, _VIDA_MINIMA_S)
        return _token


async def _postear(mensaje: str, tipo: str, token: str) -> httpx.Response:
    url = f"{base_url()}/api/EnviarFlat"
    async with httpx.AsyncClient(timeout=settings.TRADITUM_TIMEOUT) as cli:
        return await cli.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            # El escapado lo hace el serializador de httpx. El documento técnico
            # trae una función para escapar a mano, pero su tabla cruza CR y LF
            # (dice que 0D es "new line" y 0A "carriage return", y es al revés),
            # así que implementarla tal cual manda los saltos invertidos y la
            # transacción termina en error.
            json={"Msg": mensaje, "MsgType": tipo},
        )


async def enviar(mensaje: str, *, tipo: str = TIPO_HL7_24) -> str:
    """Postea un mensaje al canal y devuelve la respuesta cruda.

    Reintenta una sola vez ante un 401: el token pudo haber vencido entre que se
    tomó del cache y llegó al servidor. Cualquier otro error se propaga.
    """
    if not sale_a_la_red():
        raise TraditumError(
            "El canal Traditum está en modo simulado: no se envió nada. "
            "Cambiá TRADITUM_MODO para operar de verdad."
        )

    for intento in (1, 2):
        token = await _obtener_token()
        try:
            r = await _postear(mensaje, tipo, token)
        except httpx.TimeoutException as e:
            raise TraditumError(
                "Traditum no respondió a tiempo. Reintentá en unos minutos."
            ) from e
        except httpx.HTTPError as e:
            log.error("Traditum: falla de transporte — %r", e)
            raise TraditumError(
                "No pudimos conectarnos con Traditum. Reintentá en unos minutos; "
                "si sigue igual, avisá al Colegio."
            ) from e

        if r.status_code == 401 and intento == 1:
            log.info("Traditum: token rechazado, renovando sesión y reintentando.")
            _reiniciar_sesion()
            continue

        if r.status_code == 401:
            raise TraditumError("Traditum rechazó las credenciales del Colegio.")

        try:
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.error("Traditum: HTTP %s — %s", r.status_code, r.text[:400])
            raise TraditumError(
                f"Traditum devolvió un error HTTP {r.status_code}."
            ) from e

        return _texto_de(r)

    # Inalcanzable: el bucle sale por return o por raise.
    raise TraditumError("No se pudo enviar el mensaje a Traditum.")


def _texto_de(r: httpx.Response) -> str:
    """El cuerpo como texto.

    La API devuelve el mensaje de salida escapado dentro de un JSON, pero
    algunos errores del canal vuelven en texto plano (`[#ERROR: ...#]`), así que
    se contempla el caso en vez de asumir JSON siempre.
    """
    if r.headers.get("content-type", "").startswith("application/json"):
        try:
            datos = r.json()
        except ValueError:
            return r.text
        # El propio mensaje puede venir como string suelto o dentro de un objeto.
        if isinstance(datos, str):
            return datos
        if isinstance(datos, dict):
            for clave in ("Msg", "msg", "Mensaje", "resultado"):
                if isinstance(datos.get(clave), str):
                    return datos[clave]
        return str(datos)
    return r.text
