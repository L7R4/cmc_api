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

# class DebitoCreditoOut(BaseModel):
#     id: int
#     tipo: Literal["d","c"]
#     id_atencion: int
#     obra_social_id: int
#     periodo: str
#     monto: Decimal
#     observacion: Optional[str] = None
#     creado_timestamp: Optional[str] = None
#     created_by_user: int
#     detalle_id: Optional[int] = None
#     liquidacion_id: Optional[int] = None
#     medico_id: Optional[int] = None

#     class Config:
#         from_attributes = True

class DebCreResumenOut(BaseModel):
    liquidacion_id: int
    nro_liquidacion: Optional[str] = None
    total_bruto: float
    total_debitos: float
    total_neto: float

# ---- Schemas by_detalle ----
class DebCreByDetalleIn(BaseModel):
    tipo: Literal["d","c","n"]  # 'n' = quitar DC
    monto: Decimal = Decimal("0")
    observacion: Optional[str] = None
    created_by_user: int

class DebCreByDetalleOut(BaseModel):
    det_id: int
    debito_credito_id: Optional[int]
    tipo: Optional[Literal["d","c"]] = None
    monto: Optional[Decimal] = None
    observacion: Optional[str] = None


class DebCreRowOut(BaseModel):
    det_id: int
    tipo: Literal["N", "D", "C"]
    monto: float
    obs: Optional[str] = None
    importe: float
    pagado: float  # por si lo querés mostrar (igual hoy lo tenés en 0 en la vista)
    total: float   # ESTE es el que pintás en la columna "Total"

class DebCreResumenOut(BaseModel):
    liquidacion_id: int
    nro_liquidacion: Optional[str] = None
    total_bruto: float
    total_debitos: float
    total_neto: float

class DebCreByDetalleRecalcOut(BaseModel):
    det_id: int
    debito_credito_id: Optional[int] = None
    row: DebCreRowOut
    resumen: DebCreResumenOut