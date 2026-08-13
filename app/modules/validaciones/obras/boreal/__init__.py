"""Boreal Salud (O.S. 285) — carga manual, con coseguro y orden/receta."""
from app.modules.validaciones.obras.manual import ValidadorManual

BOREAL = ValidadorManual(
    285,
    "Boreal Salud",
    descuenta_coseguro=True,
    requiere_autorizacion=True,
    requiere_nombre=True,
    admite_orden=True,
)
