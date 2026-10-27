import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class CompensacionCreate(BaseModel):
    medico_id: int
    # Con signo: positivo a favor del socio, negativo en contra. Nunca cero.
    importe: Decimal = Field(..., description="Con signo: + a favor del socio, − en contra")
    leyenda: str = Field(..., min_length=1, max_length=160, description="Lo que dice el recibo")
    nota_interna: Optional[str] = Field(None, max_length=500)
    # Dónde queda: pendiente (se pasa a un pago después), directo al pago abierto o pagada en caja.
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
    """Solo una compensación pendiente se edita."""
    medico_id: Optional[int] = None
    importe: Optional[Decimal] = None
    leyenda: Optional[str] = Field(None, min_length=1, max_length=160)
    nota_interna: Optional[str] = Field(None, max_length=500)

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


class CompensacionRead(BaseModel):
    id: int
    medico_id: int
    medico_nombre: Optional[str] = None
    medico_nro_socio: Optional[int] = None
    importe: Decimal
    leyenda: str
    nota_interna: Optional[str] = None
    estado: str
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
