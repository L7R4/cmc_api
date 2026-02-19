from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field


class OverrideValores(BaseModel):
    monto: Optional[Decimal] = None
    porcentaje: Optional[Decimal] = None


# ---- Descuentos ----
class DescuentoBase(BaseModel):
    nombre: str = Field(..., max_length=200)
    nro_colegio: int
    precio: float = 0.0
    porcentaje: float = 0.0


class DescuentoIn(DescuentoBase):
    pass


class DescuentoUpdate(BaseModel):
    nombre: Optional[str] = Field(None, max_length=200)
    nro_colegio: Optional[int] = None
    precio: Optional[float] = None
    porcentaje: Optional[float] = None


class DescuentoOut(DescuentoBase):
    id: int

    class Config:
        from_attributes = True


class DescuentoInPatch(BaseModel):
    precio: Optional[float] = None
    porcentaje: Optional[float] = None


class EspecialidadOut(BaseModel):
    id: int
    id_colegio_espe: int
    nombre: str

    class Config:
        from_attributes = True


class AsignacionesOut(BaseModel):
    conceps: List[int] = Field(default_factory=list)
    espec: List[int] = Field(default_factory=list)
