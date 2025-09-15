# app/schemas/descuentos.py
from decimal import Decimal
from pydantic import BaseModel, Field
from typing import Optional, List

class DescuentoBase(BaseModel):
    nombre: str = Field(..., max_length=200)
    nro_colegio: int                      # ← nro de concepto interno
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


# Schema para el momento de mostrar los conceptos/especialidades del medico
class AsignacionesOut(BaseModel):
    conceps: List[int] = Field(default_factory=list)  # números de concepto
    espec: List[int] = Field(default_factory=list)    # IDs de especialidad