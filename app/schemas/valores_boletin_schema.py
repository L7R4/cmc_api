# app/schemas.py
from datetime import date
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, field_serializer

class ValoresBoletinOut(BaseModel):
    id: int
    nro_obrasocial: int
    obra_social: Optional[str] = None

    nivel: int
    fecha_cambio: Optional[date]

    # valores
    consulta: Decimal
    galeno_quirurgico: Decimal
    gastos_quirurgicos: Decimal
    galeno_practica: Decimal
    galeno_radiologico: Decimal
    gastos_radiologico: Decimal
    gastos_bioquimicos: Decimal
    otros_gastos: Decimal
    galeno_cirugia_adultos: Decimal
    galeno_cirugia_infantil: Decimal
    consulta_especial: Decimal

    categoria_a: str
    categoria_b: str
    categoria_c: str

    @field_serializer(
        "consulta", "galeno_quirurgico", "gastos_quirurgicos",
        "galeno_practica", "galeno_radiologico", "gastos_radiologico",
        "gastos_bioquimicos", "otros_gastos", "galeno_cirugia_adultos",
        "galeno_cirugia_infantil", "consulta_especial"
    )
    def _ser_decimal(self, v: Decimal) -> float:
        return float(v)

    class Config:
        from_attributes = True
