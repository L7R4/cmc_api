from __future__ import annotations
from typing import Optional, List
from decimal import Decimal
from pydantic import BaseModel, Field, validator
from enum import Enum
import re

# --------- helpers ----------
# _RX_PERIODO = re.compile(r"^\s*(\d{4})[-/]?(\d{1,2})\s*$")

# def _normalizar_periodo(v: str) -> str:
#     m = _RX_PERIODO.match(v or "")
#     if not m:
#         raise ValueError("Periodo inválido; use YYYY-MM.")
#     y, mo = int(m.group(1)), int(m.group(2))
#     if y < 1900 or y > 3000 or not (1 <= mo <= 12):
#         raise ValueError("Periodo fuera de rango.")
#     return f"{y:04d}-{mo:02d}"

class EstadoResumen(str, Enum):
    abierto = "a"     # abierto
    cerrado = "c"     # cerrado
    emitido = "e"     # emitido/facturado

# --------- LiquidacionResumen ---------
class LiquidacionResumenBase(BaseModel):
    mes: int = Field(..., ge=1, le=12)
    anio: int = Field(..., ge=1900, le=3000)
    estado: EstadoResumen = EstadoResumen.abierto
    cierre_timestamp: Optional[str] = None

class LiquidacionResumenCreate(LiquidacionResumenBase):
    # Totales se inician en 0; no se editan por POST
    pass

class LiquidacionResumenUpdate(BaseModel):
    mes: Optional[int] = Field(None, ge=1, le=12)
    anio: Optional[int] = Field(None, ge=1900, le=3000)
    estado: Optional[EstadoResumen] = None
    cierre_timestamp: Optional[str] = None

class LiquidacionResumenRead(BaseModel):
    id: int
    mes: int
    anio: int
    total_bruto: Decimal
    total_debitos: Decimal
    total_deduccion: Decimal
    estado: EstadoResumen
    cierre_timestamp: Optional[str]

    class Config:
        from_attributes = True

# --------- Liquidacion (hija) ---------
class LiquidacionBase(BaseModel):
    resumen_id: int
    obra_social_id: int
    mes_periodo: int
    anio_periodo: int
    nro_liquidacion: Optional[str] = None


class LiquidacionCreate(LiquidacionBase):
    # Totales se inician en 0; no se editan por POST
    pass

class LiquidacionUpdate(BaseModel):
    obra_social_id: Optional[int] = None
    mes_periodo: Optional[int] =None
    anio_periodo: Optional[int] =None
    nro_liquidacion: Optional[str] = None

class LiquidacionRead(BaseModel):
    id: int
    resumen_id: int
    obra_social_id: int
    mes_periodo: int
    anio_periodo: int
    nro_liquidacion: Optional[str]
    total_bruto: Decimal
    total_debitos: Decimal
    total_neto: Decimal

    class Config:
        from_attributes = True

# --------- composiciones de lectura ---------
class LiquidacionResumenWithItems(LiquidacionResumenRead):
    liquidaciones: List[LiquidacionRead] = []
