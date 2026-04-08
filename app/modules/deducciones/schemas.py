import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class OverrideValores(BaseModel):
    monto: Optional[Decimal] = None
    porcentaje: Optional[Decimal] = None


# ---- Descuentos ----
class DescuentoBase(BaseModel):
    nombre: str = Field(..., max_length=200)
    nro_colegio: int
    precio: float = 0.0
    porcentaje: float = 0.0


class DescuentoIn(DescuentoBase):
    pass


class DescuentoUpdate(BaseModel):
    nombre: Optional[str] = Field(None, max_length=200)
    nro_colegio: Optional[int] = None
    precio: Optional[float] = None
    porcentaje: Optional[float] = None


class DescuentoOut(DescuentoBase):
    id: int

    class Config:
        from_attributes = True


class DescuentoInPatch(BaseModel):
    precio: Optional[float] = None
    porcentaje: Optional[float] = None


class EspecialidadOut(BaseModel):
    id: int
    id_colegio_espe: int
    nombre: str

    class Config:
        from_attributes = True


class AsignacionesOut(BaseModel):
    conceps: List[int] = Field(default_factory=list)
    espec: List[int] = Field(default_factory=list)


# ---- Deduccion (unified: manual + automatico) ----

class DeduccionCreate(BaseModel):
    medico_id: int
    descuento_id: int
    monto_total: Decimal = Field(..., gt=0)
    cuotas: int = Field(1, ge=1)
    mes_inicio: int = Field(..., ge=1, le=12)
    anio_inicio: int = Field(..., ge=2000)
    pagador_medico_id: Optional[int] = None


# Keep alias for backward compat with existing route code
DeduccionProgramaCreate = DeduccionCreate


class DeduccionRead(BaseModel):
    id: int
    medico_id: int
    descuento_id: Optional[int] = None
    descuento_nombre: str
    origen: str  # "manual" | "automatico"
    estado: str
    monto_total: Optional[Decimal] = None
    monto_cuota: Optional[Decimal] = None
    calculado_total: Decimal
    cuotas_total: Optional[int] = None
    cuota_nro: int
    cuotificado: Optional[bool] = None
    grupo_id: Optional[int] = None
    mes_aplicar: Optional[int] = None
    anio_aplicar: Optional[int] = None
    pagador_medico_id: Optional[int] = None
    pago_id: Optional[int] = None
    created_at: Optional[datetime.datetime] = None

    class Config:
        from_attributes = True


# Keep alias for backward compat
DeduccionProgramaRead = DeduccionRead


class DeduccionEstadoPayload(BaseModel):
    estado: Literal["pendiente", "en_pago", "cancelado", "aplicado"]


# Keep alias
DeduccionProgramaEstadoPayload = DeduccionEstadoPayload


class DeduccionGrupoUpdate(BaseModel):
    monto_total: Decimal = Field(..., gt=0)


# Keep alias
DeduccionProgramaGrupoUpdate = DeduccionGrupoUpdate


# ---- SocioDescuento ----

class SocioDescuentoRead(BaseModel):
    id: int
    medico_id: int
    medico_nombre: Optional[str] = None
    medico_nro_socio: Optional[int] = None
    descuento_id: int
    descuento_nombre: Optional[str] = None
    descuento_precio: Optional[Decimal] = None
    descuento_porcentaje: Optional[Decimal] = None
    pagador_medico_id: Optional[int] = None
    pagador_nombre: Optional[str] = None
    pagador_nro_socio: Optional[int] = None
    fecha_alta: Optional[datetime.date] = None
    fecha_baja: Optional[datetime.date] = None

    class Config:
        from_attributes = True


class SocioDescuentoCreate(BaseModel):
    medico_id: int
    descuento_id: int
    pagador_medico_id: Optional[int] = None
    fecha_alta: Optional[datetime.date] = None
    fecha_baja: Optional[datetime.date] = None


class SocioDescuentoUpdate(BaseModel):
    """
    Solo se aplican los campos que se envíen explícitamente.
    Para quitar el pagador: enviar pagador_medico_id: null.
    """
    descuento_id: Optional[int] = None
    pagador_medico_id: Optional[int] = None
    fecha_baja: Optional[datetime.date] = None


class SocioDescuentoPagadorUpdate(BaseModel):
    pagador_medico_id: Optional[int] = None


class DeduccionItemMontoPayload(BaseModel):
    monto: Decimal = Field(..., gt=0)


class DeduccionItemEliminarResponse(BaseModel):
    id: int
    origen: str
    estado: str


# ---- Historial unificado ----

class DeduccionHistorialItem(BaseModel):
    id: int
    origen: Literal["manual", "automatico"]
    medico_id: int
    medico_nombre: str
    descuento_id: Optional[int] = None
    descuento_nombre: str
    monto: Decimal
    mes_periodo: Optional[int] = None
    anio_periodo: Optional[int] = None
    # estados: pendiente | en_pago | aplicado | cancelado | vencida
    estado: str
    pago_id: Optional[int] = None
    cuota_nro: int
    cuotas_total: Optional[int] = None
    grupo_id: Optional[int] = None
    created_at: datetime.datetime

    class Config:
        from_attributes = True


class DeduccionHistorialPage(BaseModel):
    total: int
    page: int
    size: int
    monto_total: Decimal
    items: List[DeduccionHistorialItem]


# ---- Top deudores ----

class TopDeudorItem(BaseModel):
    medico_id: int
    medico_nombre: str
    nro_socio: int
    saldo_total: Decimal


# ---- Verificar deducciones por pago ----

class DeduccionPorPagoResponse(BaseModel):
    existe: bool
    pago_id: int
    total: int
    monto_total: Decimal
    items: List[DeduccionRead]


# ---- Deshacer descuentos generados ----

class DeshacerDescuentosResponse(BaseModel):
    pago_id: int
    eliminadas: int
    monto_revertido: Decimal
