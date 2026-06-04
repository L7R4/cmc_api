from __future__ import annotations

from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class LoteAjusteCreate(BaseModel):
    """Para obtener_o_crear un lote normal."""
    obra_social_id: int
    mes_periodo: int = Field(..., ge=1, le=12)
    anio_periodo: int = Field(..., ge=1900, le=3000)


class LoteCambiarEstadoPayload(BaseModel):
    estado: Literal["A", "C", "L"]


class LoteRefacturacionCreate(BaseModel):
    """Para crear un lote de refacturación."""
    obra_social_id: int
    mes_periodo: int = Field(..., ge=1, le=12)
    anio_periodo: int = Field(..., ge=1900, le=3000)
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


class AjusteUpdate(BaseModel):
    tipo: Optional[Literal["d", "c"]] = None
    honorarios: Optional[Decimal] = Field(None, ge=0)
    gastos: Optional[Decimal] = Field(None, ge=0)
    observacion: Optional[str] = None


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
    nro_factura: Optional[str] = None
    total_debitos: Decimal
    total_creditos: Decimal


class LoteAjusteRead(BaseModel):
    id: int
    obra_social_id: int
    mes_periodo: int
    anio_periodo: int
    tipo: str
    snap_origen_id: Optional[int] = None
    estado: str
    pago_id: Optional[int] = None
    total_debitos: Decimal
    total_creditos: Decimal
    ajustes: Optional[List[AjusteRead]] = None

    class Config:
        from_attributes = True
