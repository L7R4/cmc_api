import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class AdelantoCreate(BaseModel):
    factura_id: int
    observacion: Optional[str] = Field(None, max_length=255)


class AdelantoUpdate(BaseModel):
    observacion: Optional[str] = Field(None, max_length=255)


class AdelantoItemsAdd(BaseModel):
    detalle_ids: List[int] = Field(..., min_length=1, max_length=2000)


class AdelantoEstadoPayload(BaseModel):
    estado: Literal["A", "C", "L"]


class FacturaParaAdelanto(BaseModel):
    FACTURA_ID: int
    ANIO: int
    MES: int
    NRO_FACTURA: Optional[str] = None
    TIPO_FACTURA: Optional[str] = None
    VERSION: int
    PERIODO: str
    MEDICOS: int
    DISPONIBLES: int
    IMPORTE_DISPONIBLE: Decimal
    RESERVADAS: int


class AdelantoListaRow(BaseModel):
    id: int
    estado: str
    pago_id: Optional[int] = None
    factura_id: int
    nro_factura: Optional[str] = None
    factura_version: Optional[int] = None
    obra_social_id: int
    obra_social_nombre: str
    mes_periodo: int
    anio_periodo: int
    medico_nombre: Optional[str] = None
    medico_nro_socio: Optional[int] = None
    medicos: int
    prestaciones: int
    importe: Decimal
    observacion: Optional[str] = None
    created_at: Optional[datetime.datetime] = None


class AjustePrestacion(BaseModel):
    """Un débito o crédito cargado a la prestación en un lote. Solo informa: el adelanto paga lo
    que vale la prestación y el ajuste se descuenta (o suma) cuando su lote entra a un pago."""
    ajuste_id: int
    lote_id: int
    lote_estado: str          # A abierto · C cerrado · L en un pago · AP ya aplicado
    pago_id: Optional[int] = None
    tipo: Literal["debito", "credito"]
    honorarios: Decimal
    gastos: Decimal
    total: Decimal
    observacion: Optional[str] = None


class AdelantoItemRead(BaseModel):
    id: int
    detalle_id: int
    fecha_practica: Optional[datetime.date] = None
    codigo: Optional[str] = None
    descripcion: Optional[str] = None
    medico_id: int
    medico_nro_socio: Optional[int] = None
    medico_nombre: Optional[str] = None
    funcion: Optional[str] = None
    honorarios: Decimal
    gastos: Decimal
    importe_total: Decimal
    debitos: Decimal = Decimal("0")
    creditos: Decimal = Decimal("0")
    ajustes: List[AjustePrestacion] = []


class AdelantoRead(BaseModel):
    id: int
    estado: str
    pago_id: Optional[int] = None
    factura_id: int
    nro_factura: Optional[str] = None
    factura_version: Optional[int] = None
    obra_social_id: int
    obra_social_nombre: str
    mes_periodo: int
    anio_periodo: int
    observacion: Optional[str] = None
    created_at: Optional[datetime.datetime] = None
    medicos: int
    prestaciones: int
    importe: Decimal
    # Débitos y créditos cargados a sus prestaciones (no se restan del importe: van con su lote).
    debitos: Decimal = Decimal("0")
    creditos: Decimal = Decimal("0")
    items: List[AdelantoItemRead]
    # Solo informativo: ¿hay un pago abierto al que se pueda pasar?
    pago_abierto_id: Optional[int] = None


class AdelantoItemsResultado(BaseModel):
    agregadas: int
    rechazadas: List[dict]
    # De las agregadas, cuántas tienen débitos o créditos cargados (para avisar).
    con_ajustes: int = 0


# Estado de una prestación de la factura frente a los adelantos:
#   disponible · en_este (en este adelanto) · en_otro (en otro adelanto, ver adelanto_id)
#   en_pago (ya está en un pago abierto como factura) · pagada · sin_medico · sin_importe
EstadoPrestacion = Literal[
    "disponible", "en_este", "en_otro", "en_pago", "pagada", "sin_medico", "sin_importe"
]


class PrestacionFacturaRow(BaseModel):
    id: int
    fecha_practica: Optional[datetime.date] = None
    codigo: Optional[str] = None
    descripcion: Optional[str] = None
    nro_orden: Optional[str] = None
    paciente: Optional[str] = None
    medico_id: Optional[int] = None
    medico_nro_socio: Optional[int] = None
    medico_nombre: Optional[str] = None
    funcion: Optional[str] = None
    equipo: Optional[str] = None
    importe_total: Decimal
    debitos: Decimal = Decimal("0")
    creditos: Decimal = Decimal("0")
    ajustes: List[AjustePrestacion] = []
    estado: EstadoPrestacion
    adelanto_id: Optional[int] = None
    pago_id: Optional[int] = None


class PrestacionesFacturaPage(BaseModel):
    items: List[PrestacionFacturaRow]
    total: int
    disponibles: int
    total_factura: int
