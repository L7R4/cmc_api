"""Schemas del ABM de beneficios (web admin).

El shape que consume la app móvil NO vive acá sino en modules/mobile/schemas.py
(BeneficioItem), a propósito: son contratos distintos y el móvil no debe
arrastrar campos de administración (activo, created_at...).
"""
import datetime
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.db.models.beneficios import CATEGORIAS_BENEFICIO

# Literal derivado de la tupla del modelo: una sola fuente de verdad y FastAPI
# lo publica como enum en /docs (el selector del admin lo lee de ahí).
CategoriaBeneficio = Literal[CATEGORIAS_BENEFICIO]  # type: ignore[valid-type]

_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _blank_to_none(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    s = v.strip()
    return s or None


def _normalize_color(v: Optional[str]) -> Optional[str]:
    """None/'' → None; si no, valida hex #RRGGBB y lo devuelve en mayúsculas."""
    s = _blank_to_none(v)
    if s is None:
        return None
    if not _HEX_COLOR.match(s):
        raise ValueError("Color inválido: usá formato hexadecimal #RRGGBB")
    return s.upper()


class BeneficioBase(BaseModel):
    titulo: str = Field(..., min_length=1, max_length=160)
    descripcion: str = Field(..., min_length=1, max_length=4000)
    descuento: Optional[str] = Field(None, max_length=60)
    categoria: CategoriaBeneficio
    color: Optional[str] = Field(None, description="Acento de la tarjeta, hex #RRGGBB")
    ubicacion: Optional[str] = Field(None, max_length=200)
    vigencia_hasta: Optional[datetime.date] = None
    activo: bool = True

    @field_validator("titulo", "descripcion", mode="after")
    @classmethod
    def _strip_required(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("No puede estar vacío")
        return s

    @field_validator("descuento", "ubicacion", mode="after")
    @classmethod
    def _strip_optional(cls, v: Optional[str]) -> Optional[str]:
        return _blank_to_none(v)

    @field_validator("color", mode="after")
    @classmethod
    def _valid_color(cls, v: Optional[str]) -> Optional[str]:
        return _normalize_color(v)


class BeneficioCreate(BeneficioBase):
    pass


class BeneficioUpdate(BaseModel):
    """PATCH parcial: sólo se tocan los campos presentes en el body."""
    titulo: Optional[str] = Field(None, min_length=1, max_length=160)
    descripcion: Optional[str] = Field(None, min_length=1, max_length=4000)
    descuento: Optional[str] = Field(None, max_length=60)
    categoria: Optional[CategoriaBeneficio] = None
    color: Optional[str] = Field(None, description="Acento de la tarjeta, hex #RRGGBB")
    ubicacion: Optional[str] = Field(None, max_length=200)
    vigencia_hasta: Optional[datetime.date] = None
    activo: Optional[bool] = None

    @field_validator("titulo", "descripcion", mode="after")
    @classmethod
    def _strip_required(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        if not s:
            raise ValueError("No puede estar vacío")
        return s

    @field_validator("descuento", "ubicacion", mode="after")
    @classmethod
    def _strip_optional(cls, v: Optional[str]) -> Optional[str]:
        return _blank_to_none(v)

    @field_validator("color", mode="after")
    @classmethod
    def _valid_color(cls, v: Optional[str]) -> Optional[str]:
        return _normalize_color(v)


class BeneficioOut(BaseModel):
    id: int
    titulo: str
    descripcion: str
    descuento: Optional[str] = None
    categoria: str
    color: Optional[str] = None
    ubicacion: Optional[str] = None
    vigencia_hasta: Optional[datetime.date] = None
    activo: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = {"from_attributes": True}
