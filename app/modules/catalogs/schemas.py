from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field, field_serializer, field_validator


# ── Boletin Observaciones ────────────────────────────────────────
class ObservacionIn(BaseModel):
    texto: str = Field(..., max_length=400)

    @field_validator("texto")
    @classmethod
    def trim_texto(cls, v: str) -> str:
        return v.strip()


class ObservacionOut(BaseModel):
    nro_obrasocial: int
    texto: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class PlantillaIn(BaseModel):
    texto: str = Field(..., max_length=400)

    @field_validator("texto")
    @classmethod
    def trim_texto(cls, v: str) -> str:
        return v.strip()


class PlantillaOut(BaseModel):
    id: int
    texto: str

    model_config = {"from_attributes": True}


# ── Especialidades ──────────────────────────────────────────────
class EspecialidadOut(BaseModel):
    id: int
    id_colegio_espe: int
    nombre: str
    class Config:
        from_attributes = True


class AsignacionesOut(BaseModel):
    conceps: List[int] = Field(default_factory=list)
    espec: List[int] = Field(default_factory=list)


# ── Obras Sociales ───────────────────────────────────────────────
class ObraSocialBase(BaseModel):
    NRO_OBRASOCIAL: int = Field(..., description="Número identificador de la obra social", example=0)
    OBRA_SOCIAL: str = Field(..., description="Nombre de la obra social", example="OSDE")
    MARCA: str = Field(..., description="Marca (1 char)", example="N")
    VER_VALOR: str = Field(..., description="Indicador ver valor (1 char)", example="N")


class ObraSocialCreate(ObraSocialBase):
    pass


class ObraSocialUpdate(BaseModel):
    NRO_OBRASOCIAL: Optional[int] = None
    OBRA_SOCIAL: Optional[str] = None
    MARCA: Optional[str] = None
    VER_VALOR: Optional[str] = None


class ObraSocialOut(ObraSocialBase):
    ID: int = Field(..., description="PK en la tabla obras_sociales")
    model_config = {"from_attributes": True}


# ── Valores Boletín ──────────────────────────────────────────────
class ValoresBoletinOut(BaseModel):
    id: int
    nro_obrasocial: int
    obra_social: Optional[str] = None

    nivel: int
    fecha_cambio: Optional[date]

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


# ── Valor Fijo ───────────────────────────────────────────────────
class ValorFijoOut(BaseModel):
    id: int
    nro_obra_social: int
    obra_social: Optional[str] = None
    codigo: str
    nro_especialidad: int
    fecha_cambio: str

    categoria_a: Decimal
    categoria_b: Decimal
    categoria_c: Decimal
    gastos: Decimal
    ayudante_a: Decimal
    ayudante_b: Decimal
    ayudante_c: Decimal

    @field_serializer(
        "categoria_a", "categoria_b", "categoria_c",
        "gastos", "ayudante_a", "ayudante_b", "ayudante_c",
    )
    def _ser_decimal(self, v: Decimal) -> float:
        return float(v)

    class Config:
        from_attributes = True


# ── Valor Prestación ─────────────────────────────────────────
class ValorPrestacionOut(BaseModel):
    id: int
    codigos: str
    nro_obrasocial: int
    obra_social: Optional[str] = None
    honorarios_a: Decimal
    honorarios_b: Decimal
    honorarios_c: Decimal
    gastos: Decimal
    ayudante_a: Decimal
    ayudante_b: Decimal
    ayudante_c: Decimal
    c_p_h_s: str
    fecha_cambio: Optional[date] = None

    @field_serializer(
        "honorarios_a", "honorarios_b", "honorarios_c",
        "gastos", "ayudante_a", "ayudante_b", "ayudante_c",
    )
    def _ser_decimal(self, v: Decimal) -> float:
        return float(v)

    class Config:
        from_attributes = True
