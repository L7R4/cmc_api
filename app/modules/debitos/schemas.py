from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class DebitoCreditoBase(BaseModel):
    tipo: Literal["d", "c"]
    monto: Decimal = Field(..., gt=Decimal("0"))
    observacion: Optional[str] = None


class DebCreByDetalleIn(BaseModel):
    tipo: Literal["d", "c"]
    monto: Decimal = Field(..., gt=Decimal("0"), description="Monto mayor a cero")
    observacion: Optional[str] = None
    created_by_user: Optional[int] = None


# Resumen de totales de la liquidación
class DebCreResumenOut(BaseModel):
    liquidacion_id: int
    nro_factura: Optional[str] = None
    total_bruto: float
    total_debitos: float
    total_neto: float


# Fila de detalle con DC aplicado
class DebCreRowOut(BaseModel):
    det_id: int
    dc_id: Optional[int] = None
    tipo: Literal["N", "D", "C"]
    monto: float
    obs: Optional[str] = None
    importe: float
    pagado: float
    total: float


# Respuesta de creación/actualización/eliminación de DC
class DebCreByDetalleRecalcOut(BaseModel):
    det_id: int
    dc_id: Optional[int] = None
    row: DebCreRowOut
    resumen: DebCreResumenOut


# Item individual de DC (para listado)
class DebCreItemOut(BaseModel):
    dc_id: int
    tipo: Literal["D", "C"]
    monto: float
    obs: Optional[str] = None


# Listado de DCs de un detalle
class DebCreListOut(BaseModel):
    det_id: int
    importe: float
    pagado: float
    total: float
    items: List[DebCreItemOut] = Field(default_factory=list)


# Legacy (mantener por compatibilidad si algo lo importa)
class DebitoCreditoCreateByDetalle(DebitoCreditoBase):
    detalle_id: int
    created_by_user: Optional[int] = None


class DebCreByDetalleOut(BaseModel):
    det_id: int
    debito_credito_id: Optional[int]
    tipo: Optional[Literal["d", "c"]] = None
    monto: Optional[Decimal] = None
    observacion: Optional[str] = None
