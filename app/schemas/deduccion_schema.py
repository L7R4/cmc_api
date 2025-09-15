# app/schemas.py
from datetime import date, datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

class DeduccionAplicadaOut(BaseModel):
    id: int
    resumen_id: int
    concepto: str
    monto: float
    descuento_id: Optional[int] = None
    especialidad_id: Optional[int] = None

    class Config:
        from_attributes = True


class GeneracionResultado(BaseModel):
    resumen_id: int
    tipo: Literal["descuento","especialidad"]
    id_aplicado: int
    creados: int
    omitidos: int
    total_deduccion: float   # valor final del campo en liquidacion_resumen

class OverrideValores(BaseModel):
    monto: Optional[Decimal] = None
    porcentaje: Optional[Decimal] = None
# -----------------------

class InstallmentIn(BaseModel):
    n: int
    dueDate: str
    amount: Decimal

class NuevaDeudaIn(BaseModel):
    concept: str = Field(..., description="Texto libre (ej. 'Cuota extraordinaria')")
    amount: Decimal | None = None
    mode: Literal["full","installments"] = "full"
    installments: Optional[List[InstallmentIn]] = None

class CrearDeudaOut(BaseModel):
    created: bool
    added_amount: Decimal
    saldo_total: Decimal
