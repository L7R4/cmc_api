from __future__ import annotations

import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class LoteAjusteCreate(BaseModel):
    """Para obtener_o_crear un lote normal: cuelga de una factura (original o complementaria).

    El período sale de la propia factura, no se manda.
    """
    obra_social_id: int
    factura_id: int = Field(..., description="facturacion.id_prestaciones")


class LoteCambiarEstadoPayload(BaseModel):
    estado: Literal["A", "C", "L"]


class LoteRefacturacionCreate(BaseModel):
    """Para crear un lote de refacturación: corrige una factura (original o complementaria).

    El período sale de la factura. Si no se manda `snap_origen_id`, el origen es el
    último lote de esa factura (la última refacturación, o el lote normal).
    """
    obra_social_id: int
    factura_id: int = Field(..., description="facturacion.id_prestaciones")
    snap_origen_id: Optional[int] = None


class LoteSinFacturaCreate(BaseModel):
    """Para crear un lote sin factura. No requiere período cerrado en Periodos."""
    obra_social_id: int
    mes_periodo: int = Field(..., ge=1, le=12)
    anio_periodo: int = Field(..., ge=1900, le=3000)


class AjusteCreate(BaseModel):
    tipo: Literal["d", "c"]
    medico_id: Optional[int] = None  # Requerido si no se provee id_atencion
    honorarios: Decimal = Field(default=Decimal("0.00"), ge=0)
    gastos: Decimal = Field(default=Decimal("0.00"), ge=0)
    observacion: Optional[str] = None
    id_atencion: Optional[int] = None  # Si se provee, deriva medico_id y obra_social_id automáticamente
    # % de este ítem que se paga (entre 0,01 y 200; 100 = todo; si no se manda, 100). Vale para lotes con y sin
    # factura. Al pasar el lote al pago el ítem se multiplica por él; NO se toma el de la factura.
    porcentaje_pago: Optional[Decimal] = None


class PrestacionCMCSearchRow(BaseModel):
    """Fila de resultado de búsqueda en detalle_facturacion (CMC)."""
    id: int
    cod_med: str
    medico_nombre: Optional[str] = None
    nom_ape_p: Optional[str] = None
    cod_nom: Optional[str] = None
    tpo_funcion: Optional[str] = None
    fecha_practica: Optional[str] = None
    nro_orden: Optional[str] = None
    honorarios: Optional[Decimal] = None
    gastos: Optional[Decimal] = None
    ayudante: Optional[Decimal] = None
    importe_total: Optional[Decimal] = None
    periodo: str
    cod_obr: str
    # Débitos menos créditos ya cargados sobre esta prestación (en cualquier lote).
    ya_debitado: Decimal = Decimal("0")


class AjusteUpdate(BaseModel):
    tipo: Optional[Literal["d", "c"]] = None
    honorarios: Optional[Decimal] = Field(None, ge=0)
    gastos: Optional[Decimal] = Field(None, ge=0)
    observacion: Optional[str] = None
    # Ver AjusteCreate.
    porcentaje_pago: Optional[Decimal] = None


class AjusteRead(BaseModel):
    id: int
    lote_id: int
    tipo: str
    medico_id: int
    obra_social_id: int
    honorarios: Decimal
    gastos: Decimal
    total: Decimal
    observacion: Optional[str] = None
    id_atencion: Optional[int] = None
    origen: str
    # % de pago del ítem; None = anterior al porcentaje por ítem (se toma como 100). Con el lote en el pago
    # (L/AP) `honorarios` y `gastos` ya vienen multiplicados por él.
    porcentaje_pago: Optional[Decimal] = None
    # Cuándo se cargó el ajuste (ajuste.created_at).
    created_at: Optional[datetime.datetime] = None
    nombre_afiliado: Optional[str] = None
    nombre_prestador: Optional[str] = None
    nro_socio: Optional[int] = None
    nro_consulta: Optional[str] = None
    tpo_funcion: Optional[str] = None
    codigo_prestacion: Optional[str] = None
    fecha_prestacion: Optional[str] = None

    class Config:
        from_attributes = True


class LoteListaRow(BaseModel):
    """Vista enriquecida para el listado de lotes (sección débitos/créditos)."""
    id: int
    tipo: str
    estado: str
    mes_periodo: int
    anio_periodo: int
    pago_id: Optional[int] = None
    obra_social_id: int
    obra_social_nombre: str
    factura_id: Optional[int] = None
    snap_origen_id: Optional[int] = None
    nro_factura: Optional[str] = None
    total_debitos: Decimal
    total_creditos: Decimal
    # Cuándo se creó el lote (lote_ajuste.created_at).
    created_at: Optional[datetime.datetime] = None


class LoteAjusteRead(BaseModel):
    id: int
    obra_social_id: int
    mes_periodo: int
    anio_periodo: int
    tipo: str
    snap_origen_id: Optional[int] = None
    obra_social_nombre: Optional[str] = None
    factura_id: Optional[int] = None
    nro_factura: Optional[str] = None
    factura_version: Optional[int] = None
    # La factura del lote ya está en el pago al que va (o está) el lote. None = no aplica.
    factura_en_pago: Optional[bool] = None
    # La factura ya fue pagada (está en un pago cerrado o pagado que no es el del lote). Un lote con
    # factura sólo puede pasar al pago si factura_en_pago o factura_pagada. None = lote sin factura.
    factura_pagada: Optional[bool] = None
    estado: str
    pago_id: Optional[int] = None
    total_debitos: Decimal
    total_creditos: Decimal
    created_at: Optional[datetime.datetime] = None
    ajustes: Optional[List[AjusteRead]] = None

    class Config:
        from_attributes = True
