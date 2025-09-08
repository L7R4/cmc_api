# schemas/debitos_creditos.py
from pydantic import BaseModel, condecimal
from typing import Optional, Literal
from decimal import Decimal
from pydantic import BaseModel, Field, validator

class DebitoCreditoBase(BaseModel):
    tipo: Literal["d", "c"]              # d=Débito, c=Crédito
    monto: Decimal = Field(..., ge=Decimal("0"))
    observacion: Optional[str] = None

class DebitoCreditoCreateByDetalle(DebitoCreditoBase):
    detalle_id: int
    created_by_user: int

class DebitoCreditoUpdate(BaseModel):
    tipo: Optional[Literal["d", "c"]] = None
    monto: Optional[Decimal] = None
    observacion: Optional[str] = None
    # remap opcional del DC a otro detalle
    detalle_id: Optional[int] = None

class DebitoCreditoOut(BaseModel):
    id: int
    tipo: Literal["d","c"]
    id_atencion: int
    obra_social_id: int
    periodo: str
    monto: Decimal
    observacion: Optional[str] = None
    creado_timestamp: Optional[str] = None
    created_by_user: int
    
    # info de join (cómoda para el front)
    detalle_id: Optional[int] = None
    liquidacion_id: Optional[int] = None
    medico_id: Optional[int] = None

    class Config:
        from_attributes = True
