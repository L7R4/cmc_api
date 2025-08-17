from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class ConceptoBase(BaseModel):
    descripcion: str = Field(..., description="Descripción del concepto")
    codigo: Optional[int] = Field(None, description="Código interno")
    es_deduccion: int = Field(..., description="Flag: es deducción (0/1)")

class ConceptoCreate(ConceptoBase): pass
class ConceptoUpdate(BaseModel):
    descripcion: Optional[str] = None
    codigo: Optional[int] = None
    es_deduccion: Optional[int] = None

class ConceptoOut(ConceptoBase):
    id: int
    created: datetime
    modified: datetime
    class Config:
        orm_mode = True

