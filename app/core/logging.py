"""Configuración central de logging.

No genera logs nuevos: decide qué pasa con los que ya se emiten. Hasta ahora
`app/modules/avisos/push.py` y `app/modules/validaciones/sancor.py` llamaban a
`logger.warning(...)` y esas líneas caían en la config por defecto de Python —
sin timestamp, sin nombre de módulo y descartando todo lo menor a WARNING.

Un solo lugar controla nivel, formato, destino y el ruido de las librerías.

Formato de salida:

    2026-08-03 14:32:07 WARNING  app.modules.avisos.push [rid=a3f91c2b] push: chunk de 40 falló

El `rid` sale de `request.state.request_id` (ver
`app/middleware/request_context.py`) y es lo que permite cruzar una línea de log
con su fila de `audit_log`.
"""
import contextvars
import logging
import sys

from app.core.config import settings

# Se puebla en RequestContextMiddleware y lo lee el filtro de abajo, así
# cualquier logger de la app arrastra el rid sin tener que pasarlo a mano.
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.rid = request_id_ctx.get()
        return True


_FORMATO = "%(asctime)s %(levelname)-8s %(name)s [rid=%(rid)s] %(message)s"
_FECHA = "%Y-%m-%d %H:%M:%S"

# Librerías que hablan de más y taparían los logs propios.
_RUIDOSOS = {
    "sqlalchemy.engine": logging.WARNING,
    "aiomysql": logging.WARNING,
    "uvicorn.access": logging.WARNING,
    "multipart": logging.WARNING,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
}


def setup_logging() -> None:
    nivel = logging.INFO if settings.IS_PRODUCTION else logging.DEBUG

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMATO, datefmt=_FECHA))
    handler.addFilter(_RequestIdFilter())

    root = logging.getLogger()
    root.setLevel(nivel)
    # Uvicorn ya instaló sus handlers; los reemplazamos para no duplicar líneas.
    root.handlers = [handler]

    for nombre, lvl in _RUIDOSOS.items():
        logging.getLogger(nombre).setLevel(lvl)

    logging.getLogger(__name__).info(
        "logging configurado (nivel=%s, entorno=%s)",
        logging.getLevelName(nivel),
        settings.ENV,
    )
