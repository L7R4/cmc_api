from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

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
    deducciones_dirty: bool = False
    # Totales calculados on-the-fly
    total_bruto: Decimal = Decimal("0")
    total_debitos: Decimal = Decimal("0")
    total_creditos: Decimal = Decimal("0")
    total_neto: Decimal = Decimal("0")
    total_deduccion: Decimal = Decimal("0")

    class Config:
        from_attributes = True


# ================================================
# PagoMedico
# ================================================

class PagoMedicoRead(BaseModel):
    id: int
    pago_id: int
    medico_id: int
    honorarios: Decimal
    gastos: Decimal
    bruto: Decimal
    debitos: Decimal
    creditos: Decimal
    reconocido: Decimal
    deducciones: Decimal
    neto_a_pagar: Decimal
    estado: str
    detalle_json: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


# ================================================
# Recibo
# ================================================

class ReciboRead(BaseModel):
    id: int
    nro_recibo: str
    pago_id: int
    medico_id: int
    pago_medico_id: Optional[int] = None
    total_neto: Decimal
    estado: str
    emision_timestamp: Optional[datetime.datetime] = None
    detalle_json: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class GenerarRecibosPayload(BaseModel):
    medico_ids: Optional[List[int]] = Field(
        None,
        description="IDs (PK) de los médicos a generar recibo. Si se omite, genera para todos.",
    )


EstadoReciboEditable = Literal["en_revision", "liquidado", "emitido"]


class EditarEstadoRecibosPayload(BaseModel):
    recibo_ids: List[int] = Field(..., min_length=1)
    estado: EstadoReciboEditable


class EliminarRecibosPayload(BaseModel):
    recibo_ids: List[int] = Field(..., min_length=1)


# ================================================
# Vista previa del pago
# ================================================

class VistaPreviaLiqItem(BaseModel):
    liquidacion_id: int
    obra_social_id: int
    obra_social_nombre: str
    nro_factura: Optional[str]
    mes_periodo: int
    anio_periodo: int
    total_honorarios: Decimal
    total_gastos: Decimal
    total_bruto: Decimal
    total_debitos: Decimal
    total_creditos: Decimal
    total_reconocido: Decimal
    total_neto: Decimal


class VistaPreviaLiqTotales(BaseModel):
    total_honorarios: Decimal
    total_gastos: Decimal
    total_bruto: Decimal
    total_debitos: Decimal
    total_creditos: Decimal
    total_reconocido: Decimal
    total_neto: Decimal


class VistaPreviaLiqSection(BaseModel):
    items: List[VistaPreviaLiqItem]
    totales: VistaPreviaLiqTotales


class VistaPreviewDedItem(BaseModel):
    descuento_id: Optional[int]
    descuento_nombre: str
    cantidad_socios: int
    total_monto: Decimal


class VistaPreviewDedTotales(BaseModel):
    total_monto: Decimal


class VistaPreviewDedSection(BaseModel):
    items: List[VistaPreviewDedItem]
    totales: VistaPreviewDedTotales


class VistaPreviewLoteItem(BaseModel):
    lote_id: int
    tipo: str
    obra_social_id: int
    obra_social_nombre: str
    mes_periodo: int
    anio_periodo: int
    estado: str
    total_debitos: Decimal
    total_creditos: Decimal


class VistaPreviewLoteTotales(BaseModel):
    total_debitos: Decimal
    total_creditos: Decimal


class VistaPreviewLoteSection(BaseModel):
    items: List[VistaPreviewLoteItem]
    totales: VistaPreviewLoteTotales


class PagoVistaPreviaRead(BaseModel):
    pago_id: int
    mes: int
    anio: int
    descripcion: Optional[str]
    estado: str
    liquidaciones: VistaPreviaLiqSection
    deducciones: VistaPreviewDedSection
    lotes: VistaPreviewLoteSection
