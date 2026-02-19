from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field


class DebitoCreditoBase(BaseModel):
    tipo: Literal["d", "c"]
    monto: Decimal = Field(..., ge=Decimal("0"))
    observacion: Optional[str] = None


class DebitoCreditoCreateByDetalle(DebitoCreditoBase):
    detalle_id: int
    created_by_user: int


class DebitoCreditoUpdate(BaseModel):
    tipo: Optional[Literal["d", "c"]] = None
    monto: Optional[Decimal] = None
    observacion: Optional[str] = None


class DebCreResumenOut(BaseModel):
    liquidacion_id: int
    nro_liquidacion: Optional[str] = None
    total_bruto: float
    total_debitos: float
    total_neto: float


class DebCreByDetalleIn(BaseModel):
    tipo: Literal["d", "c", "n"]
    monto: Decimal = Decimal("0")
    observacion: Optional[str] = None
    created_by_user: int


class DebCreByDetalleOut(BaseModel):
    det_id: int
    debito_credito_id: Optional[int]
    tipo: Optional[Literal["d", "c"]] = None
    monto: Optional[Decimal] = None
    observacion: Optional[str] = None


class DebCreRowOut(BaseModel):
    det_id: int
    tipo: Literal["N", "D", "C"]
    monto: float
    obs: Optional[str] = None
    importe: float
    pagado: float
    total: float


class DebCreByDetalleRecalcOut(BaseModel):
    det_id: int
    debito_credito_id: Optional[int] = None
    row: DebCreRowOut
    resumen: DebCreResumenOut
