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
    # La parte de total_bruto que viene de adelantos.
    total_adelantos: Decimal = Decimal("0")
    # Compensaciones del pago, con signo (+ a favor de socios, − en contra). Ya están en total_neto.
    total_compensaciones: Decimal = Decimal("0")
    # `total_debitos` incluye el saldo arrastrado de los médicos (como el sistema anterior); acá va separado.
    total_debitos_os: Decimal = Decimal("0")
    total_arrastres: Decimal = Decimal("0")
    # Del saldo arrastrado, lo que de verdad se cobra en este pago (el resto vuelve al mes siguiente como monto negativo).
    total_arrastres_cobrado: Decimal = Decimal("0")
    # Lo que los médicos que deben más de lo que produjeron dejan para el pago siguiente (sólo con el pago cerrado).
    total_negativo_proximo_mes: Decimal = Decimal("0")
    # Neto de todos los médicos tal como se les paga (suma de los netos por médico): total_neto + monto negativo.
    total_a_pagar: Decimal = Decimal("0")

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
    # Número del concepto para el Colegio (conceptos.nro_colegio), no el id de la tabla.
    nro_colegio: Optional[int] = None
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
    # Factura a la que está atada (refacturaciones y lotes con factura), si la tiene.
    nro_factura: Optional[str] = None
    # Lo que el lote suma o resta en honorarios y en gastos (créditos +, débitos −).
    total_honorarios: Decimal = Decimal("0")
    total_gastos: Decimal = Decimal("0")
    total_debitos: Decimal
    total_creditos: Decimal


class VistaPreviewLoteTotales(BaseModel):
    total_debitos: Decimal
    total_creditos: Decimal


class VistaPreviewLoteSection(BaseModel):
    items: List[VistaPreviewLoteItem]
    totales: VistaPreviewLoteTotales


class VistaPreviewAdelantoItem(BaseModel):
    adelanto_id: int
    estado: str
    obra_social_id: int
    obra_social_nombre: str
    nro_factura: Optional[str] = None
    mes_periodo: int
    anio_periodo: int
    prestaciones: int
    medicos: int
    total_honorarios: Decimal
    total_gastos: Decimal
    total_bruto: Decimal


class VistaPreviewAdelantoTotales(BaseModel):
    total_bruto: Decimal = Decimal("0")


class VistaPreviewAdelantoSection(BaseModel):
    items: List[VistaPreviewAdelantoItem] = []
    totales: VistaPreviewAdelantoTotales = VistaPreviewAdelantoTotales()


class VistaPreviewCompensacionItem(BaseModel):
    compensacion_id: int
    estado: str
    medico_id: int
    medico_nombre: str
    leyenda: str
    importe: Decimal


class VistaPreviewCompensacionTotales(BaseModel):
    a_favor: Decimal = Decimal("0")
    en_contra: Decimal = Decimal("0")
    neto: Decimal = Decimal("0")


class VistaPreviewCompensacionSection(BaseModel):
    items: List[VistaPreviewCompensacionItem] = []
    totales: VistaPreviewCompensacionTotales = VistaPreviewCompensacionTotales()


class VistaPreviaResumen(BaseModel):
    """Las cifras del encabezado del pago, para que la vista previa muestre el mismo total a pagar."""
    total_bruto: Decimal = Decimal("0")
    total_debitos_os: Decimal = Decimal("0")
    total_arrastres: Decimal = Decimal("0")
    total_arrastres_cobrado: Decimal = Decimal("0")
    total_creditos: Decimal = Decimal("0")
    total_compensaciones: Decimal = Decimal("0")
    total_deduccion: Decimal = Decimal("0")
    total_neto: Decimal = Decimal("0")
    total_negativo_proximo_mes: Decimal = Decimal("0")
    total_a_pagar: Decimal = Decimal("0")


class PagoVistaPreviaRead(BaseModel):
    pago_id: int
    mes: int
    anio: int
    descripcion: Optional[str]
    estado: str
    cantidad_socios: int = 0
    liquidaciones: VistaPreviaLiqSection
    deducciones: VistaPreviewDedSection
    lotes: VistaPreviewLoteSection
    resumen: "VistaPreviaResumen"
    adelantos: VistaPreviewAdelantoSection = VistaPreviewAdelantoSection()
    compensaciones: VistaPreviewCompensacionSection = VistaPreviewCompensacionSection()


# ================================================
# Informe por tipo de pago
# ================================================

TipoInforme = Literal["santander", "otros_bancos", "cuit_30", "cheques"]


class InformeMedicoRow(BaseModel):
    medico_id: int
    nro_socio: int
    nombre: str
    cuit: Optional[str]
    cbu: Optional[str]
    cuenta_bancaria: Optional[str]
    neto_a_pagar: Decimal

    class Config:
        from_attributes = True


class InformePagoRead(BaseModel):
    pago_id: int
    tipo: str
    total_neto: Decimal
    cantidad: int
    medicos: List[InformeMedicoRow]
