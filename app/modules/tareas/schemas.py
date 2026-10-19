"""Schemas del cuaderno de tareas del Inicio.

Los topes (140 caracteres, 30 tareas) son los mismos que en el front
(pages/Dashboard/useTareasDelDia.ts).
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.db.models.tareas_dia import PRIORIDADES, TAREAS_MAX, TEXTO_MAX

# Literal derivado de la tupla del modelo: una sola fuente de verdad.
Prioridad = Literal[PRIORIDADES]  # type: ignore[valid-type]


def _limpiar(v: str) -> str:
    s = v.strip()
    if not s:
        raise ValueError("No puede estar vacío")
    return s


# Sólo viajan id, texto, hecha y prioridad: `updated_at` se expira tras un UPDATE y
# leerlo en una sesión async sin recargar tira MissingGreenlet.
class TareaOut(BaseModel):
    id: int
    texto: str
    hecha: bool
    prioridad: Prioridad

    model_config = {"from_attributes": True}


class TareaCreate(BaseModel):
    texto: str = Field(..., min_length=1, max_length=TEXTO_MAX)
    prioridad: Prioridad = "media"

    @field_validator("texto", mode="after")
    @classmethod
    def _texto(cls, v: str) -> str:
        return _limpiar(v)


class TareaUpdate(BaseModel):
    """PATCH parcial: sólo viaja lo que cambió."""

    texto: Optional[str] = Field(None, min_length=1, max_length=TEXTO_MAX)
    hecha: Optional[bool] = None
    prioridad: Optional[Prioridad] = None

    @field_validator("texto", mode="after")
    @classmethod
    def _texto(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else _limpiar(v)


class TareaImportada(BaseModel):
    texto: str = Field(..., min_length=1, max_length=TEXTO_MAX)
    hecha: bool = False
    prioridad: Prioridad = "media"

    @field_validator("texto", mode="after")
    @classmethod
    def _texto(cls, v: str) -> str:
        return _limpiar(v)


class ImportarIn(BaseModel):
    """Lo que había en el navegador antes de que las tareas vivieran en la base."""

    tareas: list[TareaImportada] = Field(..., max_length=TAREAS_MAX)
