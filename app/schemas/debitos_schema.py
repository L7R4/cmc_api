from pydantic import BaseModel
from decimal import Decimal
from datetime import datetime
from typing import List, Optional



class DebitoBase(BaseModel):
    socio_id: int
    mes: int
    anio: int
    nomenclador_codigo: str
    paciente: str
    cantidad: int
    honorarios: Decimal
    gastos: Decimal
    antiguedad: Decimal
    procentaje: Decimal
    nro_orden: Optional[str] = None
    obra_social_id: int
    facturacion_id: Optional[int] # Tiene que ser opcional porque puede ser un débito que no se haya facturado
    liquidacion_id: Optional[int] = None

    

class DebitoCreate(DebitoBase): pass

class DebitoUpdate(BaseModel):
    id: int
    socio_id: Optional[int] = None
    mes: Optional[int] = None
    anio: Optional[int] = None
    nomenclador_codigo: Optional[str] = None
    paciente: Optional[str] = None
    cantidad: Optional[int] = None
    honorarios: Optional[Decimal] = None
    gastos: Optional[Decimal] = None
    antiguedad: Optional[Decimal] = None
    procentaje: Optional[Decimal] = None
    nro_orden: Optional[str] = None
    obra_social_id: Optional[int] = None
    facturacion_id: Optional[int] = None
    liquidacion_id: Optional[int] = None

class DebitoOut(DebitoBase):
    id: int
    created: datetime
    modified: datetime
    deleted: Optional[datetime]
    class Config: 
        orm_mode = True

