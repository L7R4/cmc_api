"""Schemas del ABM de avisos (web admin).

El shape que consume la app móvil NO vive acá sino en modules/mobile/schemas.py
(AvisoItem), igual que con beneficios: son contratos distintos y el móvil no debe
arrastrar campos de administración (activo, push_estado, enviado_por...).
"""
import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.db.models.avisos_push import TIPOS_AVISO

# Literal derivado de la tupla del modelo: una sola fuente de verdad y FastAPI lo
# publica como enum en /docs (el selector del panel lo lee de ahí).
TipoAviso = Literal[TIPOS_AVISO]  # type: ignore[valid-type]

# Topes de UX, no de la columna (la columna da margen): los proveedores de push
# truncan el título cerca de los 65 caracteres y el cuerpo cerca de los 240.
# Mismos valores que TITULO_MAX / MENSAJE_MAX en el front (avisos.types.ts).
TITULO_MAX = 80
MENSAJE_MAX = 240


class AvisoCreate(BaseModel):
    titulo: str = Field(..., min_length=1, max_length=TITULO_MAX)
    mensaje: str = Field(..., min_length=1, max_length=MENSAJE_MAX)
    tipo: TipoAviso = "General"

    @field_validator("titulo", "mensaje", mode="after")
    @classmethod
    def _strip_required(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("No puede estar vacío")
        return s


class AvisoUpdate(BaseModel):
    """PATCH parcial. Sirve sobre todo para bajar un aviso del app (activo=False)
    sin borrar el registro; el texto se puede corregir aunque ya esté publicado."""

    titulo: Optional[str] = Field(None, min_length=1, max_length=TITULO_MAX)
    mensaje: Optional[str] = Field(None, min_length=1, max_length=MENSAJE_MAX)
    tipo: Optional[TipoAviso] = None
    activo: Optional[bool] = None

    @field_validator("titulo", "mensaje", mode="after")
    @classmethod
    def _strip_required(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        if not s:
            raise ValueError("No puede estar vacío")
        return s


class AvisoOut(BaseModel):
    id: int
    titulo: str
    mensaje: str
    tipo: str
    publicado_at: datetime.datetime
    activo: bool
    push_estado: str
    push_error: Optional[str] = None
    destinatarios: Optional[int] = None
    enviado_por: Optional[int] = None
    # Nombre del admin que lo publicó, resuelto por el router (no está en la tabla).
    enviado_por_nombre: Optional[str] = None
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = {"from_attributes": True}
