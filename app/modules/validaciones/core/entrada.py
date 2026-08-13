"""Traduce el payload de red (`PrestacionCreate`) al `Entrada` propio de cada
obra social, y cualquier error de validación a un 422 en español — sin esto,
un `ValidationError` de pydantic se escapa como 500 (`app/core/errors.py` no
tiene handler para `ValidationError`).
"""
from typing import TypeVar

from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.validaciones.schemas import EntradaBase, PrestacionCreate

E = TypeVar("E", bound=EntradaBase)


def parsear_entrada(modelo: type[E], payload: PrestacionCreate) -> E:
    try:
        # `extra="ignore"` es el default de pydantic v2: cada O.S. declara sólo
        # los campos que le importan y los de las demás se descartan solos.
        return modelo.model_validate(payload.model_dump())
    except ValidationError as e:
        raise HTTPException(422, _primer_mensaje(e)) from e


def _primer_mensaje(e: ValidationError) -> str:
    """El primer error, sin el prefijo que pydantic le agrega a los `ValueError`
    de un `field_validator` — así el texto sale igual al que tiraba `service.py`
    a mano con `HTTPException(422, "...")`."""
    msg = str(e.errors()[0].get("msg", "")).removeprefix("Value error, ")
    return msg or "Datos de la prestación inválidos."
