"""Manejo global de excepciones.

Dos objetivos, y el segundo es el que importa para operar:

1. **No filtrar estructura interna.** Una `SQLAlchemyError` sin capturar salía
   como 500 y, según la configuración de Uvicorn, podía llevarse el stack trace
   hasta el cliente. Ahora el detalle va solo al log.

2. **Que un error sea rastreable.** La respuesta incluye el `request_id`, y la
   misma línea de log lleva el traceback completo con ese mismo id. El usuario
   reporta "me dio error a3f91c2b" y eso alcanza para encontrar:

       SELECT * FROM audit_log WHERE request_id = 'a3f91c2b...';   → quién y qué mandó
       grep a3f91c2b <log>                                          → por qué falló

Sin el `request_id` en ambos lados, el log y la auditoría son dos islas.
"""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.core.logging import request_id_ctx

logger = logging.getLogger(__name__)


def _rid(request: Request) -> str:
    """Id de la petición, y lo repone en el ContextVar antes de loguear.

    Hace falta reponerlo: estos handlers los invoca ServerErrorMiddleware, que
    está POR FUERA de RequestContextMiddleware. Cuando la excepción sube, el
    `finally` de ese middleware ya hizo `reset()` del ContextVar, así que sin
    esto la línea del error — justo la que importa — saldría con `rid=-` y no
    se podría cruzar con `audit_log`.
    """
    rid = getattr(request.state, "request_id", "-")
    request_id_ctx.set(rid)
    return rid


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(SQLAlchemyError)
    async def _sqlalchemy_error(request: Request, exc: SQLAlchemyError):
        rid = _rid(request)
        logger.exception(
            "error de base de datos en %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Error al acceder a la base de datos.",
                "request_id": rid,
            },
            headers={"X-Request-ID": rid},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        rid = _rid(request)
        # .exception() incluye el traceback completo — es lo único que sirve
        # para arreglar el bug, y es justamente lo que hoy se perdía.
        logger.exception(
            "excepción no capturada en %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Error interno del servidor.",
                "request_id": rid,
            },
            headers={"X-Request-ID": rid},
        )
