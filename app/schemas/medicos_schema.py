# app/schemas.py
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional,Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

class UserOut(BaseModel):
    id: int
    nro_socio: int
    nombre: Optional[str] = None
    scopes: List[str] = []
    role: Optional[str] = None 


class UserEnvelope(BaseModel):
    user: UserOut

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



class MedicoListRow(BaseModel):
    id: int
    nro_socio: Optional[int] = None
    nombre: str
    matricula_prov: Optional[int] = None
    mail_particular: Optional[str] = None
    tele_particular: Optional[str] = None
    fecha_ingreso: Optional[str] = None
    documento: Optional[str] = None

    # nuevo:
    activo: bool = False
    existe: Optional[str] = None  # opcional, solo informativo

    @field_validator("nro_socio", mode="before")
    @classmethod
    def _coerce_nro_socio(cls, v):
        if v is None:
            return None
        if isinstance(v, (bytes, bytearray, memoryview)):
            v = bytes(v).decode("utf-8", errors="ignore")
        s = str(v).strip()
        if s == "" or s == "0":
            return None
        try:
            return int(s)
        except Exception:
            return None


    @field_validator("fecha_ingreso", mode="before")
    @classmethod
    def _coerce_fecha_ingreso(cls, v):
        if v is None:
            return None
        if isinstance(v, (date, datetime)):
            return v.strftime("%Y-%m-%d")
        s = str(v).strip()
        if not s or s.startswith("0000"):
            return None
        return s[:10]

    @field_validator("documento", mode="before")
    @classmethod
    def _coerce_documento(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        if s in ("", "0"):
            return None
        return s

class EspecialidadOut(BaseModel):
    id_colegio: Optional[int] = None
    n_resolucion: Optional[str] = None
    fecha_resolucion: Optional[date] = None
    adjunto: Optional[str] = None
    adjunto_url: Optional[str] = None
    especialidad_nombre: Optional[str] = None
    id_colegio_label: Optional[str] = None

class MedicoDetailOut(BaseModel):
    # --- básicos ---
    id: int
    nro_socio: Optional[int] = None
    name: str
    nombre_: Optional[str] = None
    apellido: Optional[str] = None
    matricula_prov: Optional[int] = None
    matricula_nac: Optional[int] = None
    telefono_consulta: Optional[str] = None
    domicilio_consulta: Optional[str] = None
    mail_particular: Optional[str] = None
    sexo: Optional[str] = None
    tipo_doc: Optional[str] = None
    documento: Optional[str] = None
    cuit: Optional[str] = None
    provincia: Optional[str] = None
    codigo_postal: Optional[str] = None
    categoria: Optional[str] = None
    existe: Optional[str] = None
    fecha_nac: Optional[date] = None

    # --- personales extra ---
    localidad: Optional[str] = None
    domicilio_particular: Optional[str] = None
    tele_particular: Optional[str] = None
    celular_particular: Optional[str] = None

    # --- profesionales extra ---
    titulo: Optional[str] = None
    fecha_recibido: Optional[date] = None
    fecha_matricula: Optional[date] = None
    nro_resolucion: Optional[str] = None
    fecha_resolucion: Optional[date] = None
    especialidades: List[EspecialidadOut] = []

    # --- impositivos ---
    condicion_impositiva: Optional[str] = None
    anssal: Optional[int] = None
    vencimiento_anssal: Optional[date] = None
    malapraxis: Optional[str] = None
    vencimiento_malapraxis: Optional[date] = None
    cobertura: Optional[int] = None
    vencimiento_cobertura: Optional[date] = None
    cbu: Optional[str] = None
    observacion: Optional[str] = None

    # --- adjuntos (paths/URLs relativos) ---
    attach_titulo: Optional[str] = None
    attach_matricula_nac: Optional[str] = None
    attach_matricula_prov: Optional[str] = None
    attach_resolucion: Optional[str] = None
    attach_habilitacion_municipal: Optional[str] = None
    attach_cuit: Optional[str] = None
    attach_condicion_impositiva: Optional[str] = None
    attach_anssal: Optional[str] = None
    attach_malapraxis: Optional[str] = None
    attach_cbu: Optional[str] = None
    attach_dni: Optional[str] = None

    @field_validator("telefono_consulta","domicilio_consulta","mail_particular","sexo","tipo_doc","documento","cuit","provincia","codigo_postal","categoria","existe","localidad","domicilio_particular","tele_particular","celular_particular","titulo","nro_resolucion","attach_titulo","attach_matricula_nac","attach_matricula_prov","attach_resolucion","attach_habilitacion_municipal","attach_cuit","attach_condicion_impositiva","attach_anssal","attach_malapraxis","attach_cbu","attach_dni",mode="before")
    @classmethod
    def _coerce_str(cls, v):
        if v is None:
            return None
        if isinstance(v, (bytes, bytearray, memoryview)):
            v = bytes(v).decode("utf-8", errors="ignore")
        s = str(v).strip()
        # valores “basura” típicos del legacy
        if s in ("", "0", "@"):
            return None
        return s
    

    @field_validator(
        "telefono_consulta", "domicilio_consulta", "mail_particular",
        "sexo", "tipo_doc", "documento", "cuit", "provincia",
        "codigo_postal", "categoria", "existe",
        "localidad", "domicilio_particular", "tele_particular",
        "celular_particular", "titulo", "nro_resolucion",
        mode="before",
    )
    @classmethod
    def _coerce_str(cls, v):
        if v is None:
            return None
        if isinstance(v, (bytes, bytearray, memoryview)):
            v = bytes(v).decode("utf-8", errors="ignore")
        s = str(v).strip()
        # valores “basura” típicos del legacy
        if s in ("", "0", "@"):
            return None
        return s

    @field_validator(
        "fecha_recibido", "fecha_matricula", "fecha_nac",
        "vencimiento_anssal", "vencimiento_malapraxis", "vencimiento_cobertura",
        mode="before"
    )
    @classmethod
    def _fix_zero_dates(cls, v):
        if isinstance(v, str) and (v == "0000-00-00" or v.startswith("0000-")):
            return None
        return v

class MedicoDebtOut(BaseModel):
    has_debt: bool = False
    amount: Decimal = Decimal("0")
    last_invoice: Optional[str] = None   # "YYYY-MM"
    since: Optional[str] = None          # "YYYY-MM-DD"


def _none_if_dashish(v: Any):
    if isinstance(v, str) and v.strip() in {"—", "-", "N/A", "n/a"}:
        return None
    return v

def _none_if_empty(v: Any):
    # Convierte "" o "   " -> None
    if isinstance(v, str) and v.strip() == "":
        return None
    return v

def _date_ymd_or_none(v: Any):
    # Acepta "YYYY-MM-DD" o None; si viene "" -> None
    v = _none_if_empty(v)
    if v is None:
        return None
    # Dejá que el endpoint lo parsee luego con date.fromisoformat,
    # acá solo validamos que tenga pinta de fecha "YYYY-MM-DD"
    if isinstance(v, str) and len(v) == 10 and v[4] == "-" and v[7] == "-":
        return v
    return v  # lo demás lo toma el endpoint y lo normaliza

class MedicoUpdateIn(BaseModel):
    name: Optional[str] = None
    nombre_: Optional[str] = None
    apellido: Optional[str] = None
    sexo: Optional[str] = None
    documento: Optional[str] = None
    cuit: Optional[str] = None
    fecha_nac: Optional[str] = None
    existe: Optional[str] = None
    provincia: Optional[str] = None
    localidad: Optional[str] = None
    codigo_postal: Optional[str] = None
    domicilio_particular: Optional[str] = None
    tele_particular: Optional[str] = None
    celular_particular: Optional[str] = None
    mail_particular: Optional[str] = None

    nro_socio: Optional[int] = None
    categoria: Optional[str] = None
    titulo: Optional[str] = None
    matricula_prov: Optional[int] = None
    matricula_nac: Optional[int] = None
    fecha_recibido: Optional[str] = None
    fecha_matricula: Optional[str] = None
    domicilio_consulta: Optional[str] = None
    telefono_consulta: Optional[str] = None

    condicion_impositiva: Optional[str] = None
    anssal: Optional[Union[int, str]] = None
    cobertura: Optional[Union[int, str]] = None
    vencimiento_anssal: Optional[str] = None
    malapraxis: Optional[str] = None
    vencimiento_malapraxis: Optional[str] = None
    vencimiento_cobertura: Optional[str] = None
    cbu: Optional[str] = None
    observacion: Optional[str] = None

    model_config = ConfigDict(extra="ignore")  # ignora keys desconocidas

    # Sanitizadores globales para strings vacíos
    @field_validator(
        "name","nombre_","apellido","sexo","documento","cuit","existe","provincia",
        "localidad","codigo_postal","domicilio_particular","tele_particular",
        "celular_particular","mail_particular","nro_socio","categoria","titulo",
        "matricula_prov","matricula_nac","domicilio_consulta",
        "telefono_consulta","condicion_impositiva","malapraxis","cobertura",
        "cbu","observacion",
        mode="before"
    )

    @classmethod
    def _blank_dash_to_none(cls, v):
        v = _none_if_empty(v)
        v = _none_if_dashish(v)
        return v

    # Fechas como string (aceptá "" también)
    @field_validator(
        "fecha_nac","fecha_recibido","fecha_matricula",
        "vencimiento_anssal","vencimiento_malapraxis","vencimiento_cobertura",
        mode="before"
    )
    @classmethod
    def _date_str_ok(cls, v):
        return _date_ymd_or_none(v)

    @field_validator("anssal", "cobertura", mode="before")
    @classmethod
    def _intish(cls, v):
        v = _none_if_empty(_none_if_dashish(v))
        if v is None:
            return None
        if isinstance(v, int):
            return v
        s = str(v).strip().replace(".", "").replace(",", "")
        return int(s) if s.isdigit() else None

class MedicoUpdateOut(BaseModel):
    name: Optional[str] = None
    nombre_: Optional[str] = None
    apellido: Optional[str] = None
    sexo: Optional[str] = None
    documento: Optional[str] = None
    cuit: Optional[str] = None
    fecha_nac: Optional[str] = None
    existe: Optional[str] = None
    provincia: Optional[str] = None
    localidad: Optional[str] = None
    codigo_postal: Optional[str] = None
    domicilio_particular: Optional[str] = None
    tele_particular: Optional[str] = None
    celular_particular: Optional[str] = None
    mail_particular: Optional[str] = None

    nro_socio: Optional[int] = None
    categoria: Optional[str] = None
    titulo: Optional[str] = None
    matricula_prov: Optional[int] = None
    matricula_nac: Optional[int] = None
    fecha_recibido: Optional[str] = None
    fecha_matricula: Optional[str] = None
    domicilio_consulta: Optional[str] = None
    telefono_consulta: Optional[str] = None

    condicion_impositiva: Optional[str] = None
    anssal: Optional[Union[int, str]] = None
    cobertura: Optional[Union[int, str]] = None
    vencimiento_anssal: Optional[str] = None
    malapraxis: Optional[str] = None
    vencimiento_malapraxis: Optional[str] = None
    vencimiento_cobertura: Optional[str] = None
    cbu: Optional[str] = None
    observacion: Optional[str] = None

class DoctorStatsPointOut(BaseModel):
    month: str                      # "YYYY-MM"
    consultas: int
    facturado: float
    obras: Dict[str, float] = Field(default_factory=dict)


class ConceptoAplicacionOut(BaseModel):
    resumen_id: int
    periodo: str
    created_at: Optional[datetime] = None
    monto_aplicado: Decimal
    porcentaje_aplicado: Decimal

class MedicoConceptoOut(BaseModel):
    concepto_tipo: Literal["desc","esp"]
    concepto_id: int
    concepto_nro_colegio: Optional[int] = None
    concepto_nombre: Optional[str] = None
    saldo: Decimal
    aplicaciones: List[ConceptoAplicacionOut] = []

class AsociarConceptoIn(BaseModel):
    concepto_tipo: Literal["desc","esp"]
    concepto_id: int

class MedicoEspecialidadOut(BaseModel):
    id: int                          # ID de la especialidad (ID colegio)
    nombre: Optional[str] = None     # nombre de la especialidad
    n_resolucion: Optional[str] = None
    fecha_resolucion: Optional[str] = None
    adjunto_id: Optional[int] = None
    adjunto_url: Optional[str] = None

# ==================================
class CEAppOut(BaseModel):
    resumen_id: int
    periodo: str
    created_at: Optional[str] = None
    monto_aplicado: float
    porcentaje_aplicado: float

class ConceptRecordOut(BaseModel):
    concepto_tipo: Literal["desc", "esp"]
    concepto_id: int
    concepto_nro_colegio: Optional[int] = None
    concepto_nombre: Optional[str] = None
    saldo: float
    aplicaciones: List[CEAppOut] = Field(default_factory=list)

class CEStoreOut(BaseModel):
    conceps: List[int] = Field(default_factory=list)
    espec: List[int] = Field(default_factory=list)

class CEBundleOut(BaseModel):
    store: CEStoreOut
    conceptos: List[ConceptRecordOut] = Field(default_factory=list)

class PatchCEIn(BaseModel):
    concepto_tipo: Literal["desc", "esp"]
    concepto_id: int
    op: Literal["add", "remove"] = "add"

class CEBundlePatchIn(BaseModel):
    concepto_tipo: Literal["desc", "esp"]
    concepto_id: int # desc = nro_colegio; esp = Especialidad.ID
    op: Literal["add", "remove"] = "add"


class MedicoDocOut(BaseModel):
    id: int
    label: str                # label crudo tal como está en la DB
    pretty_label: str         # label formateado para UI
    file_name: str            # original_name
    url: str                  # "/uploads/medicos/2446/xxx.pdf"
    content_type: Optional[str] = None
    size: Optional[int] = None

class AsignarEspecialidadIn(BaseModel):
    op: Literal["add", "remove", "update"] = "add"
    id_colegio: int                       # ID_COLEGIO_ESPE
    n_resolucion: Optional[str] = None
    fecha_resolucion: Optional[str] = None  # "YYYY-MM-DD" (texto; aceptamos None)
    adjunto_id: Optional[int] = None        # Documento.id (opcional)