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

class CuotaConfig(BaseModel):
    """Configuración individual de cada cuota al crear una deducción manual."""
    paga_por_caja: bool = False


class DeduccionCreate(BaseModel):
    medico_id: int
    descuento_id: int
    monto_total: Decimal = Field(..., gt=0)
    mes_inicio: int = Field(..., ge=1, le=12)
    anio_inicio: int = Field(..., ge=2000)
    pagador_medico_id: Optional[int] = None
    # Cada elemento = una cuota. len(cuotas) determina la cantidad total.
    cuotas: List[CuotaConfig] = Field(default_factory=lambda: [CuotaConfig()], min_length=1)


# Keep alias for backward compat with existing route code
DeduccionProgramaCreate = DeduccionCreate


class DeduccionRead(BaseModel):
    id: int
    medico_id: int
    descuento_id: Optional[int] = None
    descuento_nombre: str
    origen: str  # "manual" | "automatico"
    estado: str
    paga_por_caja: bool = False
    monto_total: Optional[Decimal] = None
    monto_cuota: Optional[Decimal] = None
    calculado_total: Decimal
    monto_aplicado: Decimal = Decimal("0.00")
    cuotas_total: Optional[int] = None
    cuota_nro: int
    cuotificado: Optional[bool] = None
    mes_aplicar: Optional[int] = None
    anio_aplicar: Optional[int] = None
    pagador_medico_id: Optional[int] = None
    generado_en_pago_id: Optional[int] = None
    created_at: Optional[datetime.datetime] = None

    class Config:
        from_attributes = True


# Keep alias for backward compat
DeduccionProgramaRead = DeduccionRead


class DeduccionEstadoPayload(BaseModel):
    estado: Literal["pendiente", "en_pago", "cancelado", "aplicado"]


# Keep alias
DeduccionProgramaEstadoPayload = DeduccionEstadoPayload


class DeduccionUpdate(BaseModel):
    """Body para PATCH /deducciones/{id}. Enviar al menos uno de los campos."""
    estado: Optional[Literal["pendiente", "en_pago", "cancelado", "aplicado"]] = None
    monto: Optional[Decimal] = Field(None, gt=0)
    paga_por_caja: Optional[bool] = None


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
    paga_por_caja: bool = False

    class Config:
        from_attributes = True


class SocioDescuentoCreate(BaseModel):
    medico_id: int
    descuento_id: int
    pagador_medico_id: Optional[int] = None
    fecha_alta: Optional[datetime.date] = None
    fecha_baja: Optional[datetime.date] = None
    paga_por_caja: bool = False


class SocioDescuentoUpdate(BaseModel):
    """
    Solo se aplican los campos que se envíen explícitamente.
    Para quitar el pagador: enviar pagador_medico_id: null.
    """
    descuento_id: Optional[int] = None
    pagador_medico_id: Optional[int] = None
    fecha_baja: Optional[datetime.date] = None
    paga_por_caja: Optional[bool] = None


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
    paga_por_caja: bool = False
    medico_id: int
    medico_nombre: str
    descuento_id: Optional[int] = None
    descuento_nombre: str
    monto: Decimal
    saldo_pendiente: Decimal = Decimal("0.00")
    mes_periodo: Optional[int] = None
    anio_periodo: Optional[int] = None
    # estados: pendiente | en_pago | aplicado | cancelado | vencida | eliminado
    estado: str
    cuota_nro: int
    cuotas_total: Optional[int] = None
    generado_en_pago_id: Optional[int] = None
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


# ---- Deducciones aplicadas por pago ----

class DeduccionAplicadaItem(BaseModel):
    medico_id: int
    medico_nombre: str
    medico_nro_socio: int
    descuento_id: Optional[int]
    descuento_nombre: str
    monto_aplicado: Decimal


class DeduccionesAplicadasResponse(BaseModel):
    pago_id: int
    pago_estado: str
    total_items: int
    total_aplicado: Decimal
    items: List[DeduccionAplicadaItem]
