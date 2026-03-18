from __future__ import annotations

from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class LoteAjusteCreate(BaseModel):
    """Para obtener_o_crear un lote normal."""
    obra_social_id: int
    mes_periodo: int = Field(..., ge=1, le=12)
    anio_periodo: int = Field(..., ge=1900, le=3000)


class LoteRefacturacionCreate(BaseModel):
    """Para crear un lote de refacturación."""
    obra_social_id: int
    mes_periodo: int = Field(..., ge=1, le=12)
    anio_periodo: int = Field(..., ge=1900, le=3000)
    snap_origen_id: Optional[int] = None


class AjusteCreate(BaseModel):
    tipo: Literal["d", "c"]
    medico_id: int
    monto: Decimal = Field(..., gt=0)
    observacion: Optional[str] = None
    id_atencion: Optional[int] = None


class AjusteUpdate(BaseModel):
    tipo: Optional[Literal["d", "c"]] = None
    monto: Optional[Decimal] = Field(None, gt=0)
    observacion: Optional[str] = None


class AjusteRead(BaseModel):
    id: int
    lote_id: int
    tipo: str
    medico_id: int
    obra_social_id: int
    monto: Decimal
    observacion: Optional[str] = None
    id_atencion: Optional[int] = None
    origen: str

    class Config:
        from_attributes = True


class LoteAjusteRead(BaseModel):
    id: int
    obra_social_id: int
    mes_periodo: int
    anio_periodo: int
    tipo: str
    snap_origen_id: Optional[int] = None
    estado: str
    pago_id: Optional[int] = None
    total_debitos: Decimal
    total_creditos: Decimal
    ajustes: Optional[List[AjusteRead]] = None

    class Config:
        from_attributes = True
