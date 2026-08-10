import asyncio
import json
import time
import uuid

from app.core.security import decode_token
from app.db.database import AsyncSessionLocal
from app.db.models import AuditLog

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_EXCLUDED_PREFIXES = ("/uploads", "/docs", "/redoc", "/openapi.json")

_MAX_BODY_CHARS = 4096
_MAX_ERROR_DETAIL_CHARS = 500
_REDACTED_KEYS = {
    "password", "passwd", "clave", "token", "refresh_token",
    "access_token", "authorization", "secret", "cbu",
}

# Referencias fuertes a las tareas fire-and-forget: sin esto, asyncio puede
# recolectar la task a mitad de ejecución si nadie más la referencia.
_background_tasks: set[asyncio.Task] = set()


def _should_audit(method: str, path: str) -> bool:
    if any(path.startswith(p) for p in _EXCLUDED_PREFIXES):
        return False
    if path.startswith("/auth/"):
        return True
    return path.startswith("/api/") and method in _MUTATING_METHODS


def _redact(value):
    if isinstance(value, dict):
        return {
            k: ("***" if k.lower() in _REDACTED_KEYS else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _build_request_body(raw: bytes, content_type: str) -> str | None:
    if not raw:
        return None
    if "multipart/form-data" in content_type:
        return "<multipart omitido>"
    if "application/json" not in content_type:
        return None

    try:
        parsed = json.loads(raw)
        text = json.dumps(_redact(parsed), ensure_ascii=False)
    except (ValueError, UnicodeDecodeError):
        text = raw.decode("utf-8", errors="replace")

    if len(text) > _MAX_BODY_CHARS:
        return text[:_MAX_BODY_CHARS] + "...<truncado>"
    return text


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    for k, v in headers:
        if k.lower() == name:
            return v.decode("latin-1")
    return None


def _decode_user(authorization: str | None) -> tuple[int | None, str | None]:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None, None
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token)
    except Exception:
        # Token vencido/inválido: la petición se audita igual, sin usuario.
        return None, None
    if payload.get("type") != "access":
        return None, None
    sub = payload.get("sub")
    nro_socio = int(sub) if sub is not None and str(sub).isdigit() else None
    return nro_socio, payload.get("role")


def _route_template(scope: dict) -> str | None:
    try:
        route = scope.get("route")
        return getattr(route, "path", None) if route else None
    except Exception:
        return None


async def _persist(entry: dict) -> None:
    try:
        async with AsyncSessionLocal() as db:
            db.add(AuditLog(**entry))
            await db.commit()
    except Exception:
        # Un fallo al auditar nunca debe impactar al cliente: la respuesta
        # real ya se envió en este punto.
        pass


class AuditMiddleware:
    """Registra en `audit_log` las peticiones mutadoras (POST/PUT/PATCH/DELETE)
    de /api/* y todo /auth/*. Ver docs/api/auditoria.md."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not _should_audit(scope["method"], scope["path"]):
            await self.app(scope, receive, send)
            return

        method = scope["method"]
        path = scope["path"]
        headers = scope.get("headers", [])
        content_type = _header(headers, b"content-type") or ""
        authorization = _header(headers, b"authorization")
        client = scope.get("client")

        body_chunks: list[bytes] = []

        async def receive_wrapper():
            message = await receive()
            if message["type"] == "http.request":
                body_chunks.append(message.get("body", b""))
            return message

        state = {"status_code": 500}
        error_chunks: list[bytes] = []

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                state["status_code"] = message["status"]
            elif message["type"] == "http.response.body" and state["status_code"] >= 400:
                error_chunks.append(message.get("body", b""))
            await send(message)

        start = time.perf_counter()
        excepcion: BaseException | None = None
        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except BaseException as exc:
            # La respuesta 500 la arma ServerErrorMiddleware, que está por fuera
            # nuestro: este middleware nunca ve ese body. Sin capturar acá, la
            # fila quedaba con error_detail=NULL justo en los errores que más
            # importan. Se re-lanza intacta.
            excepcion = exc
            raise
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            nro_socio, role = _decode_user(authorization)

            error_detail = None
            if excepcion is not None:
                error_detail = (
                    f"{type(excepcion).__name__}: {excepcion}"
                )[:_MAX_ERROR_DETAIL_CHARS]
            elif state["status_code"] >= 400 and error_chunks:
                try:
                    parsed = json.loads(b"".join(error_chunks))
                    if isinstance(parsed, dict) and "detail" in parsed:
                        error_detail = str(parsed["detail"])[:_MAX_ERROR_DETAIL_CHARS]
                except (ValueError, UnicodeDecodeError):
                    pass

            entry = dict(
                method=method,
                path=path,
                route=_route_template(scope),
                query_params=(scope.get("query_string") or b"").decode("latin-1")[:1000] or None,
                user_id=None,  # se resuelve por join a listado_medico.NRO_SOCIO al consultar
                nro_socio=nro_socio,
                role=role,
                status_code=state["status_code"],
                duration_ms=duration_ms,
                ip=client[0] if client else None,
                user_agent=(_header(headers, b"user-agent") or "")[:255] or None,
                request_body=_build_request_body(b"".join(body_chunks), content_type),
                error_detail=error_detail,
                # El mismo id que viaja en X-Request-ID y que lleva cada línea
                # de log de esta petición. Lo asigna RequestContextMiddleware;
                # el fallback cubre el caso de que ese middleware no esté.
                request_id=scope.get("state", {}).get("request_id")
                or uuid.uuid4().hex,
            )

            task = asyncio.create_task(_persist(entry))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
