from fastapi import BaseModel, Field
from typing import Optional,List
from datetime import datetime


# -----------------------
#  PERIODO
# -----------------------
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
    facturaciones: Optional[List[int]] = Field(None, description="Lista de IDs de facturaciones asociadas")
    class Config:
        orm_mode = True
