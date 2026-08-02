"""
MOBILE APP (cmc-app) ONLY — response schemas for the doctor-facing mobile BFF.

These mirror the shapes the mobile app declares in its `src/api/types.ts`, so the
app can flip a repo from mock → live with no UI change. Kept intentionally slim
(only the fields the app renders) versus the richer website schemas.
"""
import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class ObraSocialItem(BaseModel):
    """Slim obra-social row for the app's pickers (vs. the website's heavy ObraSocialOut)."""
    nro_obra_social: int
    nombre: str


class NomencladorItem(BaseModel):
    """Slim nomenclador row for the app's price search."""
    id: int
    codigo: str
    descripcion: str
    categoria: Optional[str] = None
    complejidad: Optional[str] = None
    sin_restriccion_especialidad: bool
    activo: bool

    model_config = {"from_attributes": True}


class ConsultaComunItem(BaseModel):
    """Valor de la consulta del boletín (código 420351) por obra social."""
    nro_obra_social: int
    nombre: str
    valor: str
    fecha_cambio: Optional[str] = None


class GalenoValorItem(BaseModel):
    """Un galeno/gasto de una OS con su valor. `valor_hasta` sólo cuando los
    niveles difieren (se muestra como rango); si no, es un único valor."""
    codigo: str
    nombre: str
    valor: str
    valor_hasta: Optional[str] = None


class GalenoBoletinItem(BaseModel):
    """Galenos vigentes de una obra social, agrupados en galenos y gastos."""
    nro_obra_social: int
    nombre: str
    galenos: List[GalenoValorItem]
    gastos: List[GalenoValorItem]


class EspecialidadMedicoItem(BaseModel):
    id_colegio: int
    nombre: str
    principal: bool


class MedicoProfileItem(BaseModel):
    """Perfil view-only del médico logueado (mirror de MedicoProfile en types.ts)."""
    id: int
    nro_socio: int
    nombre: str
    apellido: Optional[str] = None
    documento: Optional[int] = None
    tipo_doc: Optional[str] = None
    cuit: Optional[str] = None
    matricula_prov: Optional[str] = None
    matricula_nac: Optional[str] = None
    email: Optional[str] = None
    telefono: Optional[str] = None
    celular: Optional[str] = None
    domicilio: Optional[str] = None
    localidad: Optional[str] = None
    provincia: Optional[str] = None
    codigo_postal: Optional[str] = None
    sexo: Optional[str] = None
    fecha_nac: Optional[datetime.date] = None
    existe: str
    categoria: Optional[str] = None
    condicion_impositiva: Optional[str] = None
    monotributista: Optional[bool] = None
    vitalicio: bool = False
    fecha_ingreso: Optional[datetime.date] = None
    fecha_matricula: Optional[datetime.date] = None
    especialidades: List[EspecialidadMedicoItem] = []
    vencimiento_anssal: Optional[datetime.date] = None
    vencimiento_malapraxis: Optional[datetime.date] = None
    vencimiento_cobertura: Optional[datetime.date] = None


class PadronItem(BaseModel):
    """Una obra social en cuyo padrón está inscripto el médico (solo lectura)."""
    nro_obra_social: int
    nombre: str
    categoria: Optional[str] = None
    especialidades: List[str] = []


class CredencialItem(BaseModel):
    """Credencial digital derivada del médico logueado (mirror de Credencial)."""
    nro_socio: int
    display_name: str
    titulo: str  # 'Dr.' | 'Dra.'
    documento: Optional[int] = None
    especialidad_principal: Optional[str] = None
    activo: bool
    emitida: datetime.date


class BeneficioItem(BaseModel):
    """Un beneficio/convenio para la pantalla Beneficios (mirror de Beneficio en
    types.ts). Sólo lo que el app renderiza: sin `activo` ni timestamps.

    `color` es un hex "#RRGGBB" (o null) que el app usa como acento de la
    tarjeta — un borde/fondo tenue para destacar el beneficio."""
    id: int
    titulo: str
    descripcion: str
    descuento: Optional[str] = None
    categoria: str
    color: Optional[str] = None
    ubicacion: Optional[str] = None
    vigencia_hasta: Optional[datetime.date] = None

    model_config = {"from_attributes": True}


class AvisoItem(BaseModel):
    """Un aviso del Colegio para la bandeja de Avisos del app. Sólo lo que el app
    renderiza: sin `activo`, sin los campos de despacho de push ni timestamps de
    auditoría.

    `tipo` es uno de TIPOS_AVISO (General/Institucional/Novedades/Beneficios/
    Urgente) y define el ícono y el color con el que el app lo muestra.
    `publicado_at` es el orden de la bandeja (más reciente primero)."""
    id: int
    titulo: str
    mensaje: str
    tipo: str
    publicado_at: datetime.datetime

    model_config = {"from_attributes": True}


class SolicitudCambioIn(BaseModel):
    """Body de "Reclamar cambios". nro_socio y medico_id NO viajan acá: se toman
    del token, para que nadie pueda abrir un reclamo a nombre de otro."""
    campo: str = Field(..., min_length=2, max_length=40)
    valor_actual: Optional[str] = Field(None, max_length=255)
    valor_propuesto: Optional[str] = Field(None, max_length=255)
    mensaje: str = Field(..., min_length=1, max_length=2000)

    @field_validator("campo", "mensaje", mode="after")
    @classmethod
    def _strip_required(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("No puede estar vacío")
        return s

    @field_validator("valor_actual", "valor_propuesto", mode="after")
    @classmethod
    def _strip_optional(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return v.strip() or None


class SolicitudCambioOutMobile(BaseModel):
    """Acuse de recibo del reclamo recién creado."""
    id: int
    campo: str
    estado: str
    created_at: datetime.datetime


class DispositivoIn(BaseModel):
    """Registro del token de push del dispositivo. El médico sale del token de
    sesión, nunca del body."""
    expo_push_token: str = Field(..., min_length=1, max_length=255)
    plataforma: Optional[str] = Field(None, max_length=20)

    @field_validator("expo_push_token", mode="after")
    @classmethod
    def _strip_token(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("No puede estar vacío")
        return s


class DispositivoOut(BaseModel):
    """Acuse del alta/baja. No devuelve el token: el app ya lo tiene."""
    registrado: bool
