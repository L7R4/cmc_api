from typing import Optional, Literal
from decimal import Decimal
from pydantic import BaseModel, Field, validator
import re

_PERIODO_RX = re.compile(r"^\s*(\d{4})[-/]?(\d{1,2})\s*$")

def _norm_periodo(v: str) -> str:
    m = _PERIODO_RX.match(v or "")
    if not m:
        raise ValueError("periodo debe ser YYYY-MM (o YYYYMM/ YYYY-M)")
    y, mth = int(m.group(1)), int(m.group(2))
    if y < 1900 or y > 3000 or not (1 <= mth <= 12):
        raise ValueError("periodo fuera de rango")
    return f"{y:04d}-{mth:02d}"

class DebitoCreditoBase(BaseModel):
    tipo: Literal["d", "c"] = Field(..., description="d=debito, c=credito")
    id_atencion: int
    obra_social_id: int
    periodo: str
    monto: Decimal = Field(..., ge=Decimal("0"))
    observacion: Optional[str] = None

    @validator("periodo")
    def _validate_periodo(cls, v: str) -> str:
        return _norm_periodo(v)

class DebitoCreditoCreate(DebitoCreditoBase):
    created_by_user: int

class DebitoCreditoUpdate(BaseModel):
    tipo: Optional[Literal["d", "c"]] = None
    id_atencion: Optional[int] = None
    obra_social_id: Optional[int] = None
    periodo: Optional[str] = None
    monto: Optional[Decimal] = Field(None, ge=Decimal("0"))
    observacion: Optional[str] = None

    @validator("periodo")
    def _validate_periodo(cls, v: Optional[str]) -> Optional[str]:
        return _norm_periodo(v) if v else v

class DebitoCreditoOut(DebitoCreditoBase):
    id: int
    creado_timestamp: Optional[str] = None
    created_by_user: int

    class Config:
        orm_mode = True