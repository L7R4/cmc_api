# app/schemas.py
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

class PeriodoBase(BaseModel):
    mes: Optional[int] = Field(None, description="Mes del periodo")
    anio: Optional[str] = Field(None, description="Año del periodo (string)")
    liquidado: int = Field(..., description="Flag liquidado (0/1)")

class PeriodoCreate(PeriodoBase): pass
class PeriodoUpdate(BaseModel):
    mes: Optional[int] = None
    anio: Optional[str] = None
    liquidado: Optional[int] = None

class PeriodoOut(PeriodoBase):
    id: int
    created: Optional[datetime]
    modified: Optional[datetime]
    class Config:
        orm_mode = True
