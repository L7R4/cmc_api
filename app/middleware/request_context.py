"""Asigna un `request_id` a cada petición y lo devuelve en `X-Request-ID`.

Es la pieza que hace cruzables la auditoría y el log. Antes el `request_id` se
generaba dentro de `AuditMiddleware`, al final de la petición y solo para armar
la fila de `audit_log`: identificaba la fila, no la petición, y no aparecía en
ningún otro lado.

Ahora se genera al principio y queda en tres lugares a la vez:

  - `request.state.request_id` — accesible desde cualquier endpoint o handler
  - el header `X-Request-ID` de la respuesta — el cliente lo puede mostrar
  - la columna `audit_log.request_id` — lo escribe `AuditMiddleware`

Con eso, "me dio error a3f91c2b" se busca en `audit_log` (quién, cuándo, qué
mandó) y en el log de la aplicación (el traceback).

Va como el middleware más externo para que el header salga en toda respuesta,
incluidas las que corta CORS.
"""
import uuid

from app.core.logging import request_id_ctx

_HEADER = b"x-request-id"


class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Si viene de un proxy que ya lo asignó, se respeta: así el ID es el
        # mismo de punta a punta cuando haya más de un servicio en el camino.
        entrante = None
        for k, v in scope.get("headers", []):
            if k.lower() == _HEADER:
                entrante = v.decode("latin-1")[:32]
                break

        request_id = entrante or uuid.uuid4().hex

        # `Request.state` lee de scope["state"]; mutamos el scope in situ para
        # que también lo vea ServerErrorMiddleware, que está por fuera nuestro.
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id

        # Y en el ContextVar, para que toda línea de log de esta petición lo
        # lleve sin que haya que pasarlo por parámetro. Cada request corre en su
        # propia Task, así que el contexto no se pisa entre peticiones.
        token = request_id_ctx.set(request_id)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((_HEADER, request_id.encode("latin-1")))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            request_id_ctx.reset(token)
