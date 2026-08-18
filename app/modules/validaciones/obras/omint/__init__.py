"""Omint (O.S. 243) — carga manual, sin coseguro ni autorización previa."""
from app.modules.validaciones.obras.manual import ValidadorManual

OMINT = ValidadorManual(
    243,
    "Omint",
    descuenta_coseguro=False,
    requiere_autorizacion=False,
    requiere_nombre=True,
)
