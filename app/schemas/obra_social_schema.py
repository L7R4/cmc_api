from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class ObraSocialBase(BaseModel):
    nombre: str = Field(..., description="Nombre de la obra social")
    codigo: Optional[int] = Field(None, description="Código externo")

class ObraSocialCreate(ObraSocialBase): pass

class ObraSocialUpdate(BaseModel):
    nombre: Optional[str] = None
    codigo: Optional[int] = None

class ObraSocialOut(ObraSocialBase):
    id: int
    created: Optional[datetime]
    modified: Optional[datetime]
    deleted: Optional[datetime]
    class Config:
        orm_mode = True

