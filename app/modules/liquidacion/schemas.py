from __future__ import annotations

import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class PreviewItem(BaseModel):
    liquidacion_id: int
    obra_social_id: int
    obra_social_nombre: Optional[str] = None
    periodo: str
    estado: Literal["A", "C"]
    nro_factura: Optional[str] = None
    total_bruto: Decimal
    total_debitos: Decimal
    total_deduccion: Decimal
    total_neto: Decimal


class PreviewTotals(BaseModel):
    cerradas_bruto: Decimal
    cerradas_debitos: Decimal
    cerradas_neto: Decimal
    abiertas_bruto: Decimal
    abiertas_debitos: Decimal
    abiertas_neto: Decimal
    resumen_deduccion: Decimal
    total_general: Decimal


class PreviewResponse(BaseModel):
    items: List[PreviewItem]
    totals: PreviewTotals


# ================================================
# LiquidacionResumen
# ================================================
class LiquidacionResumenBase(BaseModel):
    mes: int = Field(..., ge=1, le=12)
    anio: int = Field(..., ge=1900, le=3000)


class LiquidacionResumenCreate(LiquidacionResumenBase):
    pass


class LiquidacionResumenRead(BaseModel):
    id: int
    mes: int
    anio: int
    total_bruto: Decimal
    total_debitos: Decimal
    total_deduccion: Decimal
    total_neto: Decimal

    class Config:
        from_attributes = True


class LiquidacionResumenWithItems(LiquidacionResumenRead):
    liquidaciones: List[LiquidacionRead] = []


# ================================================
# Liquidacion por obra social
# ================================================
class LiquidacionBase(BaseModel):
    resumen_id: int
    obra_social_id: int
    mes_periodo: int = Field(ge=1, le=12)
    anio_periodo: int = Field(ge=1900)
    nro_factura: str


class LiquidacionCreate(LiquidacionBase):
    pass


class LiquidacionUpdate(BaseModel):
    obra_social_id: Optional[int] = None
    mes_periodo: Optional[int] = None
    anio_periodo: Optional[int] = None
    nro_factura: Optional[str] = None


class LiquidacionRead(BaseModel):
    id: int
    resumen_id: int
    obra_social_id: int
    mes_periodo: int
    anio_periodo: int
    estado: str
    nro_factura: Optional[str] = None
    # AGENT DO IT: cierre_timestamp como datetime real
    cierre_timestamp: Optional[datetime.datetime] = None
    refacturado_from: Optional[int] = None
    total_bruto: Decimal
    total_debitos: Decimal
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
    # AGENT DO IT: prestacion_id ahora es int
    prestacion_id: int
    importe: Decimal
    pagado: Decimal

    class Config:
        from_attributes = True


# ================================================
# Vista enriquecida de detalles
# ================================================
class DebitoCreditoVistaRow(BaseModel):
    """DC dentro de la vista de detalles - incluye dc_id para gestión."""
    dc_id: int
    tipo: Literal["C", "D"]
    monto: float = 0
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
    importe: float
    pagado: float
    debitos_creditos_list: List[DebitoCreditoVistaRow] = Field(default_factory=list)
    total: float


# ================================================
# Refacturar
# ================================================
class RefacturarPayload(BaseModel):
    punto_venta: str
    nro_factura: str


# ================================================
# LiquidacionMedico (AGENT DO IT: nueva entidad)
# ================================================
class LiquidacionMedicoRead(BaseModel):
    id: int
    resumen_id: int
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


class LiquidacionMedicoResumen(BaseModel):
    """Respuesta del endpoint generar_liquidacion_medico."""
    total_medicos: int
    items: List[dict]


# ================================================
# Recibo (AGENT DO IT: nueva entidad)
# ================================================
class ReciboItemRead(BaseModel):
    id: int
    recibo_id: int
    liquidacion_id: int
    concepto: str
    importe: Decimal

    class Config:
        from_attributes = True


class ReciboRead(BaseModel):
    id: int
    nro_recibo: str
    resumen_id: int
    medico_id: int
    total_neto: Decimal
    emision_timestamp: Optional[datetime.datetime] = None
    estado: str
    items: List[ReciboItemRead] = Field(default_factory=list)

    class Config:
        from_attributes = True


class ReciboAnularPayload(BaseModel):
    motivo: Optional[str] = None


# ================================================
# Compatibilidad legacy (DebitosCreditosRow sin dc_id)
# ================================================
class DebitosCreditosRow(BaseModel):
    tipo: Literal["C", "D"]
    monto: float = 0
    obs: Optional[str] = None
