from __future__ import annotations

import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class PagoCreate(BaseModel):
    anio: int = Field(..., ge=1900, le=3000)
    mes: int = Field(..., ge=1, le=12)
    descripcion: Optional[str] = None


class PagoUpdate(BaseModel):
    descripcion: Optional[str] = None


class PagoRead(BaseModel):
    id: int
    anio: int
    mes: int
    descripcion: Optional[str] = None
    estado: str
    cierre_timestamp: Optional[datetime.datetime] = None
    # Totales calculados on-the-fly
    total_bruto: Decimal = Decimal("0")
    total_debitos: Decimal = Decimal("0")
    total_creditos: Decimal = Decimal("0")
    total_neto: Decimal = Decimal("0")
    total_deduccion: Decimal = Decimal("0")

    class Config:
        from_attributes = True


class PagoMedicoRead(BaseModel):
    id: int
    pago_id: int
    medico_id: int
    bruto: Decimal
    debitos: Decimal
    creditos: Decimal
    reconocido: Decimal
    deducciones: Decimal
    neto_a_pagar: Decimal
    estado: str

    class Config:
        from_attributes = True


class PagoMedicoTotales(BaseModel):
    total_medicos: int
    total_bruto: Decimal
    total_debitos: Decimal
    total_creditos: Decimal
    total_reconocido: Decimal
    total_deducciones: Decimal
    total_neto_a_pagar: Decimal


class PagoMedicoListResponse(BaseModel):
    totales: PagoMedicoTotales
    items: List[PagoMedicoRead]
