# app/schemas.py
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator
class MedicoBase(BaseModel):
    nro_especialidad: int        = Field(..., description="Especialidad principal")
    nro_especialidad2: int       = Field(..., description="Especialidad 2")
    nro_especialidad3: int       = Field(..., description="Especialidad 3")
    nro_especialidad4: int       = Field(..., description="Especialidad 4")
    nro_especialidad5: int       = Field(..., description="Especialidad 5")
    nro_especialidad6: int       = Field(..., description="Especialidad 6")
    nro_socio: int               = Field(..., description="Número de socio")
    nombre: str                  = Field(..., description="Nombre completo")
    domicilio_consulta: str      = Field(..., description="Domicilio de consulta")
    telefono_consulta: str       = Field(..., description="Teléfono de consulta")
    matricula_prov: int          = Field(..., description="Matrícula Provincial")
    matricula_nac: int           = Field(..., description="Matrícula Nacional")
    fecha_recibido: Optional[date]        = Field(None, description="Fecha recibido expediente")
    fecha_matricula: Optional[date]       = Field(None, description="Fecha de matrícula")
    fecha_ingreso: Optional[date]         = Field(None, description="Fecha de ingreso al colegio")
    domicilio_particular: str    = Field(..., description="Domicilio particular")
    tele_particular: str         = Field(..., description="Teléfono particular")
    celular_particular: str      = Field(..., description="Celular particular")
    mail_particular: str         = Field(..., description="Email particular")
    sexo: str                    = Field(..., description="Sexo (M/F)")
    tipo_doc: str                = Field(..., description="Tipo de documento")
    documento: str               = Field(..., description="Número de documento")
    fecha_nac: Optional[date]    = Field(None, description="Fecha de nacimiento")
    cuit: str                    = Field(..., description="CUIT")
    anssal: int                  = Field(..., description="ANS Salud")
    vencimiento_anssal: Optional[date] = Field(None, description="Vencimiento ANSSAL")
    malapraxis: str              = Field(..., description="Seguro de mala praxis")
    vencimiento_malapraxis: Optional[date] = Field(None, description="Vencimiento mala praxis")
    monotributista: str          = Field(..., description="¿Es monotributista? (SI/NO)")
    factura: str                 = Field(..., description="¿Factura? (SI/NO)")
    cobertura: int               = Field(..., description="Cobertura")
    vencimiento_cobertura: Optional[date] = Field(None, description="Vencimiento cobertura")
    provincia: str               = Field(..., description="Provincia")
    codigo_postal: str           = Field(..., description="Código postal")
    vitalicio: str               = Field(..., description="¿Es vitalicio? (S/N)")
    fecha_vitalicio: Optional[date] = Field(None, description="Fecha de vitalicio")
    observacion: str             = Field(..., description="Observaciones")
    categoria: str               = Field(..., description="Categoría")
    existe: str                  = Field(..., description="¿Existe? (S/N)")
    excep_desde: str             = Field(..., description="Excepción desde (MMYYYY)")
    excep_hasta: str             = Field(..., description="Excepción hasta (MMYYYY)")
    excep_desde2: str            = Field(..., description="Segunda excepción desde")
    excep_hasta2: str            = Field(..., description="Segunda excepción hasta")
    excep_desde3: str            = Field(..., description="Tercera excepción desde")
    excep_hasta3: str            = Field(..., description="Tercera excepción hasta")
    ingresar: str                = Field(..., description="Campo de control ingreso")

    @field_validator(
        "fecha_recibido", "fecha_matricula", "fecha_ingreso",
        "fecha_nac", "vencimiento_anssal", "vencimiento_malapraxis",
        "vencimiento_cobertura", "fecha_vitalicio",
        mode="before"
    )
    @classmethod
    def _fix_zero_dates(cls, v):
        # Convierte '0000-00-00' o strings con año 0000 a None
        if isinstance(v, str) and (v == "0000-00-00" or v.startswith("0000-")):
            return None
        return v

class MedicoCreate(MedicoBase):
    """Todos los campos obligatorios para crear un Médico."""

class MedicoUpdate(BaseModel):
    """Esquema para actualizaciones parciales de Médico."""
    nro_especialidad: Optional[int] = None
    nro_especialidad2: Optional[int] = None
    nro_especialidad3: Optional[int] = None
    nro_especialidad4: Optional[int] = None
    nro_especialidad5: Optional[int] = None
    nro_especialidad6: Optional[int] = None
    nro_socio: Optional[int] = None
    nombre: Optional[str] = None
    domicilio_consulta: Optional[str] = None
    telefono_consulta: Optional[str] = None
    matricula_prov: Optional[int] = None
    matricula_nac: Optional[int] = None
    fecha_recibido: Optional[date] = None
    fecha_matricula: Optional[date] = None
    fecha_ingreso: Optional[date] = None
    domicilio_particular: Optional[str] = None
    tele_particular: Optional[str] = None
    celular_particular: Optional[str] = None
    mail_particular: Optional[str] = None
    sexo: Optional[str] = None
    tipo_doc: Optional[str] = None
    documento: Optional[str] = None
    fecha_nac: Optional[date] = None
    cuit: Optional[str] = None
    anssal: Optional[int] = None
    vencimiento_anssal: Optional[date] = None
    malapraxis: Optional[str] = None
    vencimiento_malapraxis: Optional[date] = None
    monotributista: Optional[str] = None
    factura: Optional[str] = None
    cobertura: Optional[int] = None
    vencimiento_cobertura: Optional[date] = None
    provincia: Optional[str] = None
    codigo_postal: Optional[str] = None
    vitalicio: Optional[str] = None
    fecha_vitalicio: Optional[date] = None
    observacion: Optional[str] = None
    categoria: Optional[str] = None
    existe: Optional[str] = None
    excep_desde: Optional[str] = None
    excep_hasta: Optional[str] = None
    excep_desde2: Optional[str] = None
    excep_hasta2: Optional[str] = None
    excep_desde3: Optional[str] = None
    excep_hasta3: Optional[str] = None
    ingresar: Optional[str] = None

class MedicoOut(MedicoBase):
    id: int = Field(..., description="ID del médico")
    created: datetime
    modified: datetime
    class Config:
        orm_mode = True


