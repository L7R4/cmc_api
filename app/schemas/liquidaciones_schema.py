from __future__ import annotations
from typing import Optional, List, Literal
from decimal import Decimal
from pydantic import BaseModel, Field, validator 
from enum import Enum
import re


class PreviewItem(BaseModel):
    liquidacion_id: int
    obra_social_id: int
    obra_social_nombre: Optional[str] = None
    periodo: str                              
    estado: Literal["A", "C"]
    nro_facutra: Optional[str] = None
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
    total_general: Decimal  # (cerradas_neto + abiertas_neto + resumen_deduccion)

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
    mes_periodo: Optional[int] =None
    anio_periodo: Optional[int] =None
    nro_facutra: Optional[str] = None


class LiquidacionRead(BaseModel):
    id: int
    resumen_id: int
    obra_social_id: int
    mes_periodo: int
    anio_periodo: int
    estado: str
    nro_factura: str
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
    prestacion_id: str
    importe: Decimal
    prev_detalle_id: int | None = None  # si ese campo existe en tu modelo
    pagado: Decimal
    debito_credito_id: Optional[int] = None
    class Config:
        from_attributes = True


class DebitosCreditosRow(BaseModel):
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

    debitos_creditos_list: List[DebitosCreditosRow] = Field(default_factory=list)

    total: float


class RefacturarPayload(BaseModel):
    punto_venta: str
    nro_factura: str


