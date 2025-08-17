from pydantic import BaseModel
from typing import Optional, List, Dict
from datetime import datetime
from decimal import Decimal

# ================================
#           LIQUIDACIÓN
# ================================

class LiquidacionBase(BaseModel):
    mes: Optional[int]
    anio: Optional[str]
    dgi_mes: int = 0
    dgi_anio: int = 0
    nro_liquidacion: int = 0
    estado_id: int = 1
    proceso_id: int = 0
    calculo_deducciones: int = 0
    fecha_calculo: Optional[datetime] = None
    resumen: Optional[str] = None
    proceso_cerrar_id: int = 0
    fecha_cierre: Optional[datetime] = None
    data_socio_grupo: Optional[Dict] = None
    es_visible: int = 1
    nro_inicio_cheque: int = 0
    santander_nro_inicio_cheque: int = 0
    proceso_pagos_id: int = 0

class LiquidacionCreate(LiquidacionBase):
    detalles: List["LiquidacionDetalleCreate"] = []

class LiquidacionUpdate(BaseModel):
    mes: Optional[int] = None
    anio: Optional[str] = None
    dgi_mes: Optional[int] = None
    dgi_anio: Optional[int] = None
    nro_liquidacion: Optional[int] = None
    estado_id: Optional[int] = None
    proceso_id: Optional[int] = None
    calculo_deducciones: Optional[int] = None
    fecha_calculo: Optional[datetime] = None
    resumen: Optional[str] = None
    proceso_cerrar_id: Optional[int] = None
    fecha_cierre: Optional[datetime] = None
    data_socio_grupo: Optional[Dict] = None
    es_visible: Optional[int] = None
    nro_inicio_cheque: Optional[int] = None
    santander_nro_inicio_cheque: Optional[int] = None
    proceso_pagos_id: Optional[int] = None

class LiquidacionOut(LiquidacionBase):
    id: int
    created: datetime
    modified: datetime
    detalles: List["LiquidacionDetalleOut"] = []

    class Config:
        orm_mode = True

# ================================
#      LIQUIDACIÓN DETALLE
# ================================

class LiquidacionDetalleBase(BaseModel):
    socio_id: int
    socio_modelo: str
    mes: int = 0
    anio: int = 0
    facturacion_id: Optional[int] = None
    # liquidacion_obra_id: int = 0
    liquidacion_id: Optional[int] = None
    concepto_id: Optional[int] = None
    estado_id: int = 1
    # liquidacion_estado_id: int = 0
    tipo_movimiento: str
    fact_honorarios: Decimal = Decimal("0.00")
    fact_gastos: Decimal = Decimal("0.00")
    fact_antiguedad: Decimal = Decimal("0.00")
    fact_total: Decimal = Decimal("0.00")
    porcentaje: Decimal = Decimal("100.00")
    liq_honorarios: Decimal = Decimal("0.00")
    liq_gastos: Decimal = Decimal("0.00")
    liq_antiguedad: Decimal = Decimal("0.00")
    liq_total: Decimal = Decimal("0.00")
    debito_id: Optional[int] = None
    obra_social_id: Optional[int] = None

class LiquidacionDetalleCreate(LiquidacionDetalleBase):
    pass

class LiquidacionDetalleUpdate(BaseModel):
    socio_id: Optional[int] = None
    socio_modelo: Optional[str] = None
    mes: Optional[int] = None
    anio: Optional[int] = None
    facturacion_id: Optional[int] = None
    # liquidacion_obra_id: Optional[int] = None
    liquidacion_id: Optional[int] = None
    concepto_id: Optional[int] = None
    estado_id: Optional[int] = None
    # liquidacion_estado_id: Optional[int] = None
    tipo_movimiento: Optional[str] = None
    fact_honorarios: Optional[Decimal] = None
    fact_gastos: Optional[Decimal] = None
    fact_antiguedad: Optional[Decimal] = None
    fact_total: Optional[Decimal] = None
    porcentaje: Optional[Decimal] = None
    liq_honorarios: Optional[Decimal] = None
    liq_gastos: Optional[Decimal] = None
    liq_antiguedad: Optional[Decimal] = None
    liq_total: Optional[Decimal] = None
    debito_id: Optional[int] = None
    obra_social_id: Optional[int] = None

class LiquidacionDetalleOut(LiquidacionDetalleBase):
    id: int
    created: datetime
    modified: datetime

    class Config:
        orm_mode = True


