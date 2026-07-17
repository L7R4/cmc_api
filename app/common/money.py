from decimal import Decimal, ROUND_HALF_UP

TWOPLACES = Decimal("0.01")


def quantize_money(x) -> Decimal:
    """Coerce a Decimal y redondea a 2 decimales (ROUND_HALF_UP). Todo monto que se
    persista o se devuelva en una response debe pasar por acá antes."""
    return Decimal(str(x if x is not None else 0)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
