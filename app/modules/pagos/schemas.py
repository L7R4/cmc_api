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


# ================================================
# Resumen completo del pago
# ================================================
class ResumenFacturaRow(BaseModel):
    liquidacion_id: int
    obra_social_id: int
    obra_social_nombre: str
    nro_factura: Optional[str]
    mes_periodo: int
    anio_periodo: int
    total_bruto: Decimal
    total_debitos: Decimal
    total_creditos: Decimal
    total_neto: Decimal


class ResumenConceptoDescuento(BaseModel):
    concepto_tipo: str          # "desc" | "esp"
    concepto_id: int
    nombre: str
    total_aplicado: Decimal


class ResumenTotales(BaseModel):
    total_bruto: Decimal
    total_debitos: Decimal
    total_creditos: Decimal
    total_reconocido: Decimal   # bruto - debitos + creditos
    total_deducciones: Decimal
    total_neto: Decimal         # reconocido - deducciones


class PagoResumenRead(BaseModel):
    pago_id: int
    mes: int
    anio: int
    descripcion: Optional[str]
    estado: str
    facturas: List[ResumenFacturaRow]
    conceptos_descuento: List[ResumenConceptoDescuento]
    totales: ResumenTotales


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
    total_bruto: Decimal
    total_debitos: Decimal
    total_creditos: Decimal
    total_reconocido: Decimal
    total_neto: Decimal


class VistaPreviaLiqTotales(BaseModel):
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
