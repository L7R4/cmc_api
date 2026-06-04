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

class ContactoIn(BaseModel):
    tipo: str = Field(..., pattern="^(email|telefono)$")
    valor: str = Field(..., max_length=200)
    etiqueta: Optional[str] = Field(None, max_length=80)


class ContactoSimpleOut(BaseModel):
    valor: str
    etiqueta: Optional[str] = None


class DireccionIn(BaseModel):
    provincia: Optional[str] = Field(None, max_length=100)
    localidad: Optional[str] = Field(None, max_length=100)
    direccion: Optional[str] = Field(None, max_length=200)
    codigo_postal: Optional[str] = Field(None, max_length=10)
    horario: Optional[str] = Field(None, max_length=150)


class DireccionOut(BaseModel):
    id: int
    provincia: Optional[str] = None
    localidad: Optional[str] = None
    direccion: Optional[str] = None
    codigo_postal: Optional[str] = None
    horario: Optional[str] = None


class DocumentoOut(BaseModel):
    id: int
    tipo: str
    nombre_custom: Optional[str] = None
    url: str
    created_at: datetime


class ObraSocialSimpleOut(BaseModel):
    id: int
    nro_obra_social: int
    nombre: str
    denominacion: str


class ObraSocialCreate(BaseModel):
    nro_obra_social: int = Field(..., description="Número identificador de la obra social")
    nombre: str = Field(..., max_length=45, description="Nombre de la obra social")
    marca: str = Field("N", max_length=1)
    ver_valor: str = Field("N", max_length=1)
    cuit: Optional[str] = Field(None, max_length=20)
    direccion_real: Optional[str] = Field(None, max_length=200)
    condicion_iva: Optional[str] = Field(None, pattern="^(responsable_inscripto|exento)$")
    plazo_vencimiento: Optional[int] = None
    fecha_alta_convenio: Optional[date] = None
    obra_social_principal_id: Optional[int] = None
    contactos: List[ContactoIn] = Field(default_factory=list)
    direcciones: List[DireccionIn] = Field(default_factory=list)


class ObraSocialUpdate(BaseModel):
    nro_obra_social: Optional[int] = None
    nombre: Optional[str] = Field(None, max_length=45)
    marca: Optional[str] = Field(None, max_length=1)
    ver_valor: Optional[str] = Field(None, max_length=1)
    cuit: Optional[str] = Field(None, max_length=20)
    direccion_real: Optional[str] = Field(None, max_length=200)
    condicion_iva: Optional[str] = Field(None, pattern="^(responsable_inscripto|exento)$")
    plazo_vencimiento: Optional[int] = None
    fecha_alta_convenio: Optional[date] = None
    obra_social_principal_id: Optional[int] = None
    # Si se provee, reemplaza la lista completa
    contactos: Optional[List[ContactoIn]] = None
    direcciones: Optional[List[DireccionIn]] = None


class ObraSocialOut(BaseModel):
    id: int
    nro_obra_social: int
    nombre: str
    denominacion: str
    marca: str
    ver_valor: str
    cuit: Optional[str] = None
    direccion_real: Optional[str] = None
    condicion_iva: Optional[str] = None
    plazo_vencimiento: Optional[int] = None
    fecha_alta_convenio: Optional[date] = None
    obra_social_principal_id: Optional[int] = None
    emails: List[ContactoSimpleOut] = Field(default_factory=list)
    telefonos: List[ContactoSimpleOut] = Field(default_factory=list)
    obra_social_principal: Optional[ObraSocialSimpleOut] = None
    asociadas: List[ObraSocialSimpleOut] = Field(default_factory=list)
    direccion: List[DireccionOut] = Field(default_factory=list)
    documentos: List[DocumentoOut] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


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
class ValorPrestacionIn(BaseModel):
    codigos: str
    nro_obrasocial: int
    obra_social: Optional[str] = None
    c_p_h_s: Optional[str] = ""
    honorarios_a: Decimal = Decimal("0")
    honorarios_b: Decimal = Decimal("0")
    honorarios_c: Decimal = Decimal("0")
    gastos: Decimal = Decimal("0")
    ayudante_a: Decimal = Decimal("0")
    ayudante_b: Decimal = Decimal("0")
    ayudante_c: Decimal = Decimal("0")
    fecha_cambio: Optional[date] = None
    fecha_final: Optional[date] = None


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
    fecha_final: Optional[date] = None

    @field_serializer(
        "honorarios_a", "honorarios_b", "honorarios_c",
        "gastos", "ayudante_a", "ayudante_b", "ayudante_c",
    )
    def _ser_decimal(self, v: Decimal) -> float:
        return float(v)

    class Config:
        from_attributes = True


# ── Valor Nomenclado Swiss ───────────────────────────────────
class ValorNomencladoSwissUpdate(BaseModel):
    honorarios_a: Optional[Decimal] = None
    gastos: Optional[Decimal] = None
    ayudante_a: Optional[Decimal] = None


class ValorNomencladoSwissOut(BaseModel):
    id: int
    codigo: str
    c_p_h_s: Optional[str] = None
    descripcion: Optional[str] = None
    honorarios_a: Decimal
    gastos: Decimal
    ayudante_a: Decimal

    @field_serializer("honorarios_a", "gastos", "ayudante_a")
    def _ser_decimal(self, v: Decimal) -> float:
        return float(v)

    class Config:
        from_attributes = True


# ── Valores Éticos ────────────────────────────────────────────
class ValoresEticosOut(BaseModel):
    id: int
    pdf_path: Optional[str] = None
    observaciones: Optional[str] = None
    fecha_update: Optional[datetime] = None

    class Config:
        from_attributes = True
