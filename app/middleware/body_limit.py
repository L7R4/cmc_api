"""Tope de tamaño para los bodies que no son archivos (S2 de la auditoría).

## Qué problema resuelve

Sin límite, `POST /api/valores_nm/actualizar_por_codigos` con un array de 50
millones de items hace que Uvicorn lea el body entero a memoria, Pydantic lo
valide entero, y el worker muera por OOM. Con `UVICORN_WORKERS=2`, dos requests
concurrentes bajan la API completa. No hace falta ninguna credencial para
intentarlo — basta con llegar al endpoint.

Los uploads ya estaban cubiertos (`app/common/uploads.py`, 20 MB por archivo con
validación de tipo por magic bytes). Lo que faltaba era todo lo demás: JSON,
form-urlencoded, texto plano.

## Por qué acá y no en Caddy

Hacen falta los dos, y hacen cosas distintas:

  * **Caddy** debería tener un `request_body { max_size 25MB }` como techo
    exterior, para cortar la conexión antes de que el cuerpo llegue siquiera a
    Python. Es el que protege contra el que sube gigabytes.
  * **Esto** distingue por `Content-Type`, cosa que Caddy no puede hacer sin
    duplicar la lista de rutas de subida: un `multipart/form-data` legítimo pesa
    20 MB y un JSON legítimo no llega a 400 KB. Un único número para los dos
    tendría que ser el más alto, y entonces no limita nada.

## Cómo lo hace

Dos controles, porque `Content-Length` es un dato que manda el cliente y no hay
ninguna obligación de que sea cierto:

  1. Si el header viene y se pasa, se rechaza **antes de leer un solo byte**.
     Es el caso normal y el barato.
  2. Si no viene (`Transfer-Encoding: chunked`) o miente, se acumula el cuerpo
     en memoria **hasta el límite + 1 byte**. Al pasarse se responde 413 y la
     aplicación **nunca se llega a invocar**. Sin esto, `Content-Length: 10`
     seguido de 2 GB de cuerpo pasaría el primer control.

El punto 2 buferea, y eso es deliberado: el techo de memoria por request pasa a
ser exactamente `MAX_JSON_BODY_BYTES`, que es la garantía que se buscaba. Un
intento anterior cortaba el stream con `http.disconnect` en vez de buferear, y
no servía — Starlette lo convierte en un 400 "error parsing the body" y responde
antes de que el middleware pueda decir nada. El cliente recibía un mensaje que
no explicaba el problema.

Responde **413 Payload Too Large**, que es lo que el cliente necesita para
distinguir "mandaste demasiado" de un 400 genérico de validación.
"""
import json
import logging

from app.core.config import settings

log = logging.getLogger(__name__)

# Los uploads se validan aparte, por archivo, en app/common/uploads.py. Un
# multipart de 20 MB es legítimo; uno de 20 MB de JSON no.
_TIPOS_EXENTOS = ("multipart/form-data",)

_METODOS_CON_BODY = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _exento(headers: list[tuple[bytes, bytes]]) -> bool:
    for k, v in headers:
        if k.lower() == b"content-type":
            ct = v.decode("latin-1").lower()
            return any(ct.startswith(t) for t in _TIPOS_EXENTOS)
    return False


def _content_length(headers: list[tuple[bytes, bytes]]) -> int | None:
    for k, v in headers:
        if k.lower() == b"content-length":
            try:
                return int(v)
            except ValueError:
                return None
    return None


class BodyLimitMiddleware:
    """ASGI puro, igual que RequestContextMiddleware.

    No es `BaseHTTPMiddleware` a propósito: ese materializa el body para poder
    exponerlo como `Request`, que es exactamente lo que hay que evitar cuando el
    objetivo es no leer bodies enormes.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in _METODOS_CON_BODY:
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        if _exento(headers):
            await self.app(scope, receive, send)
            return

        limite = settings.MAX_JSON_BODY_BYTES

        declarado = _content_length(headers)
        if declarado is not None and declarado > limite:
            await self._rechazar(scope, send, declarado, limite, "declarado")
            return

        # Se acumula hasta el límite. Si se pasa, se responde 413 acá y la app
        # ni se entera; si no, se le entrega el cuerpo ya leído.
        partes: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # http.disconnect: el cliente cortó. Se pasa tal cual para que
                # Starlette haga lo suyo.
                partes = []
                break
            partes.append(message.get("body", b""))
            total += len(partes[-1])
            if total > limite:
                await self._rechazar(scope, send, total, limite, "recibido")
                return
            if not message.get("more_body", False):
                break

        cuerpo = b"".join(partes)
        entregado = False

        async def receive_replay():
            nonlocal entregado
            if entregado:
                # La app ya tiene el cuerpo completo; cualquier lectura extra es
                # el final del stream.
                return {"type": "http.request", "body": b"", "more_body": False}
            entregado = True
            return {"type": "http.request", "body": cuerpo, "more_body": False}

        await self.app(scope, receive_replay, send)

    async def _rechazar(self, scope, send, tamano: int, limite: int, origen: str) -> None:
        log.warning(
            "body demasiado grande: %s %s — %s %d bytes, límite %d",
            scope.get("method"), scope.get("path"), origen, tamano, limite,
        )
        cuerpo = json.dumps({
            "detail": (
                f"El cuerpo de la petición supera el máximo de "
                f"{limite // 1024} KB. Si estás subiendo un archivo, usá el "
                f"endpoint de adjuntos; si es una carga masiva, partila en lotes."
            )
        }).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(cuerpo)).encode("latin-1")),
            ],
        })
        await send({"type": "http.response.body", "body": cuerpo})
