# app/schemas.py
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator



class DeduccionBase(BaseModel):
    socio_id: Optional[int]
    socio_modelo: str
    mes: int
    anio: int
    concepto_id: Optional[int]
    adeudado: Decimal
    cobrado: Decimal
    saldo: Decimal

class DeduccionCreate(DeduccionBase): pass
class DeduccionUpdate(BaseModel):
    adeudado: Optional[Decimal] = None
    cobrado: Optional[Decimal]  = None
    saldo: Optional[Decimal]    = None

class DeduccionOut(DeduccionBase):
    id: int
    created: Optional[datetime]
    modified: Optional[datetime]
    class Config:
        orm_mode = True


# -----------------------