"""Quién hace la petición en curso, para lo que se registra lejos del endpoint.

`AuditMiddleware` ya decodifica el token de toda petición que modifica algo; deja
acá el NRO_SOCIO y el rol para que el historial de facturación
(`app/modules/facturacion/historial.py`) sepa quién hizo cada cambio sin que cada
servicio tenga que recibir el usuario por parámetro.

Fuera de una petición (cron, scripts, tests que llaman al servicio directo) queda en
`(None, None)`: el evento se registra como hecho por el sistema.
"""
import contextvars

usuario_ctx: contextvars.ContextVar[tuple[int | None, str | None]] = contextvars.ContextVar(
    "usuario", default=(None, None)
)
