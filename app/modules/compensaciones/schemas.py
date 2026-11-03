import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


MAX_CUOTAS = 60


class CompensacionCreate(BaseModel):
    medico_id: int
    # Con signo: positivo a favor del socio, negativo en contra. Nunca cero. Es el total: en
    # cuotas se reparte en partes iguales y el resto va en la última.
    importe: Decimal = Field(..., description="Con signo: + a favor del socio, − en contra. Total del plan.")
    leyenda: str = Field(..., min_length=1, max_length=160, description="Lo que dice el recibo")
    nota_interna: Optional[str] = Field(None, max_length=500)
    cuotas: int = Field(1, ge=1, le=MAX_CUOTAS)
    # Mes de la cuota 1; las demás, una por mes. Sin mes: el del pago abierto o, si no hay, el actual.
    mes_inicio: Optional[int] = Field(None, ge=1, le=12)
    anio_inicio: Optional[int] = Field(None, ge=2000, le=2100)
    # pendiente: cada cuota entra sola al pago abierto cuando llega su mes.
    # pago: la cuota 1 va al pago abierto ya (empieza en su mes). caja: pagada en caja hoy (una cuota).
    destino: Literal["pendiente", "pago", "caja"] = "pendiente"

    @field_validator("importe")
    @classmethod
    def _importe(cls, v: Decimal) -> Decimal:
        v = v.quantize(Decimal("0.01"))
        if v == 0:
            raise ValueError("El importe no puede ser cero")
        if abs(v) >= Decimal("1000000000"):
            raise ValueError("El importe es demasiado grande")
        return v

    @field_validator("leyenda")
    @classmethod
    def _leyenda(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("La leyenda del recibo es obligatoria")
        return v


class CompensacionUpdate(BaseModel):
    """Edita el plan de la cuota. Socio, importe (total), cuotas y mes, sólo con todas las cuotas
    pendientes; la leyenda y la nota, siempre (en las cuotas que no se pagaron)."""
    medico_id: Optional[int] = None
    importe: Optional[Decimal] = None
    leyenda: Optional[str] = Field(None, min_length=1, max_length=160)
    nota_interna: Optional[str] = Field(None, max_length=500)
    cuotas: Optional[int] = Field(None, ge=1, le=MAX_CUOTAS)
    mes_inicio: Optional[int] = Field(None, ge=1, le=12)
    anio_inicio: Optional[int] = Field(None, ge=2000, le=2100)

    @field_validator("importe")
    @classmethod
    def _importe(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is None:
            return v
        v = v.quantize(Decimal("0.01"))
        if v == 0:
            raise ValueError("El importe no puede ser cero")
        if abs(v) >= Decimal("1000000000"):
            raise ValueError("El importe es demasiado grande")
        return v


class CompensacionEstadoPayload(BaseModel):
    estado: Literal["pendiente", "en_pago", "en_caja"]


class CompensacionAnularPayload(BaseModel):
    motivo: str = Field(..., min_length=1, max_length=255)
    # plan: esta cuota y las demás pendientes del plan. cuota: sólo esta.
    alcance: Literal["plan", "cuota"] = "plan"


class CompensacionRead(BaseModel):
    id: int
    medico_id: int
    medico_nombre: Optional[str] = None
    medico_nro_socio: Optional[int] = None
    plan_id: Optional[int] = None
    cuota_nro: int = 1
    cuotas_total: int = 1
    importe: Decimal
    importe_total: Optional[Decimal] = None
    leyenda: str
    nota_interna: Optional[str] = None
    estado: str
    mes_aplicar: Optional[int] = None
    anio_aplicar: Optional[int] = None
    pago_id: Optional[int] = None
    fecha_caja: Optional[datetime.date] = None
    motivo_anulacion: Optional[str] = None
    created_at: Optional[datetime.datetime] = None
    creada_por: Optional[str] = None


class CompensacionTotales(BaseModel):
    a_favor: Decimal
    en_contra: Decimal
    pendientes: int


class CompensacionLista(BaseModel):
    items: List[CompensacionRead]
    totales: CompensacionTotales
