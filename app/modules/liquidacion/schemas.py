from __future__ import annotations

import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ================================================
# Liquidacion por obra social
# ================================================
class LiquidacionCreate(BaseModel):
    pago_id: int
    obra_social_id: int
    mes_periodo: int = Field(..., ge=1, le=12)
    anio_periodo: int = Field(..., ge=1900)


class LiquidacionRead(BaseModel):
    id: int
    pago_id: int
    obra_social_id: int
    mes_periodo: int
    anio_periodo: int
    nro_factura: Optional[str] = None
    total_honorarios: Decimal
    total_gastos: Decimal
    total_bruto: Decimal
    total_debitos: Decimal
    total_creditos: Decimal
    total_neto: Decimal

    class Config:
        from_attributes = True


# ================================================
# Detalle liquidacion por obra social
# ================================================
class DetalleLiquidacionRead(BaseModel):
    id: int
    liquidacion_id: int
    medico_id: int
    obra_social_id: int
    prestacion_id: int
    honorarios: Decimal
    gastos: Decimal
    importe_total: Decimal
    pagado: Decimal

    class Config:
        from_attributes = True


# ================================================
# Vista enriquecida de detalles
# ================================================
class AjusteVistaRow(BaseModel):
    """Ajuste dentro de la vista de detalles."""
    ajuste_id: int
    tipo: Literal["C", "D"]
    honorarios: float = 0
    gastos: float = 0
    total: float = 0
    obs: Optional[str] = None


class DetalleVistaRow(BaseModel):
    det_id: int
    socio: int | str
    nombreSocio: str
    matri: int | str | None
    nroOrden: int | str
    fecha: str
    codigo: str | int
    nroAfiliado: str | None
    afiliado: str | None
    xCant: str
    porcentaje: float
    honorarios: float
    gastos: float
    coseguro: float
    importe_total: float
    pagado: float
    debitos_creditos_list: List[AjusteVistaRow] = Field(default_factory=list)
    total: float


# ================================================
# Recibo
# ================================================
class ReciboRead(BaseModel):
    id: int
    nro_recibo: str
    pago_id: int
    medico_id: int
    total_neto: Decimal
    emision_timestamp: Optional[datetime.datetime] = None
    estado: str

    class Config:
        from_attributes = True


class ReciboAnularPayload(BaseModel):
    motivo: Optional[str] = None
