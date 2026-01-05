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

FIELD_MAP = {
    # básicos
    "name": "NOMBRE",
    "nombre_": "nombre_",
    "apellido": "apellido",
    "sexo": "SEXO",
    "documento": "DOCUMENTO",
    "cuit": "CUIT",
    "fecha_nac": "FECHA_NAC",
    "existe": "EXISTE",
    "provincia": "PROVINCIA",
    "localidad": "localidad",
    "codigo_postal": "CODIGO_POSTAL",
    "domicilio_particular": "DOMICILIO_PARTICULAR",
    "tele_particular": "TELE_PARTICULAR",
    "celular_particular": "CELULAR_PARTICULAR",
    "mail_particular": "MAIL_PARTICULAR",

    # profesionales
    "nro_socio": "NRO_SOCIO",
    "categoria": "categoria",              # si en tu modelo es MAYÚS poné "CATEGORIA"
    "titulo": "titulo",
    "matricula_prov": "MATRICULA_PROV",
    "matricula_nac": "MATRICULA_NAC",
    "fecha_recibido": "FECHA_RECIBIDO",
    "fecha_matricula": "FECHA_MATRICULA",
    "domicilio_consulta": "DOMICILIO_CONSULTA",
    "telefono_consulta": "TELEFONO_CONSULTA",

    # impositivos
    "condicion_impositiva": "condicion_impositiva",
    "anssal": "ANSSAL",
    "cobertura": "COBERTURA",
    "vencimiento_anssal": "VENCIMIENTO_ANSSAL",
    "malapraxis": "MALAPRAXIS",
    "vencimiento_malapraxis": "VENCIMIENTO_MALAPRAXIS",
    "vencimiento_cobertura": "VENCIMIENTO_COBERTURA",
    "cbu": "cbu",
    "observacion": "OBSERVACION",
}

DATE_KEYS = {
    "fecha_nac","fecha_recibido","fecha_matricula","fecha_resolucion",
    "vencimiento_anssal","vencimiento_malapraxis","vencimiento_cobertura",
}

def _coerce_existe(v):
    if v is None:
        return None
    s = str(v).strip().upper()
    if s in ("S","N"):
        return s
    # acepta truthy/falsy
    return "S" if s in ("1","TRUE","T","SI","SÍ","Y","YES","ON") else "N"

def _coerce_sexo(v):
    if v is None:
        return None
    s = str(v).strip().upper()
    return s[:1] if s else None  # "M" / "F" (o lo que uses)


class MedicoBase(BaseModel):
    nro_especialidad: Optional[int]        = Field(None, description="Especialidad principal")
    nro_especialidad2: Optional[int]       = Field(None, description="Especialidad 2")
    nro_especialidad3: Optional[int]       = Field(None, description="Especialidad 3")
    nro_especialidad4: Optional[int]       = Field(None, description="Especialidad 4")
    nro_especialidad5: Optional[int]       = Field(None, description="Especialidad 5")
    nro_especialidad6: Optional[int]       = Field(None, description="Especialidad 6")
    nro_socio: Optional[int]               = Field(None, description="Número de socio")
    nombre: Optional[str]                  = Field(None, description="Nombre completo")
    nombre_: Optional[str]       = Field(None, description="Nombre alternativo")
    apellido: Optional[str]      = Field(None, description="Apellido")
    domicilio_consulta: Optional[str]      = Field(None, description="Domicilio de consulta")
    telefono_consulta: Optional[str]       = Field(None, description="Teléfono de consulta")
    matricula_prov: Optional[int]          = Field(None, description="Matrícula Provincial")
    matricula_nac: Optional[int]           = Field(None, description="Matrícula Nacional")
    fecha_recibido: Optional[date]        = Field(None, description="Fecha recibido expediente")
    fecha_matricula: Optional[date]       = Field(None, description="Fecha de matrícula")
    fecha_ingreso: Optional[date]         = Field(None, description="Fecha de ingreso al colegio")
    domicilio_particular: Optional[str]    = Field(None, description="Domicilio particular")
    tele_particular: Optional[str]         = Field(None, description="Teléfono particular")
    celular_particular: Optional[str]      = Field(None, description="Celular particular")
    mail_particular: Optional[str]         = Field(None, description="Email particular")
    sexo: Optional[str]                    = Field(None, description="Sexo (M/F)")
    tipo_doc: Optional[str]                = Field(None, description="Tipo de documento")
    documento: Optional[str]               = Field(None, description="Número de documento")
    fecha_nac: Optional[date]    = Field(None, description="Fecha de nacimiento")
    cuit: Optional[str]                    = Field(None, description="CUIT")
    condicion_impositiva: Optional[str] = Field(None, description="Condicion impositiva")
    anssal: Optional[int]                  = Field(None, description="ANS Salud")
    vencimiento_anssal: Optional[date] = Field(None, description="Vencimiento ANSSAL")
    malapraxis: Optional[str]              = Field(None, description="Seguro de mala praxis")
    vencimiento_malapraxis: Optional[date] = Field(None, description="Vencimiento mala praxis")
    monotributista: Optional[str]          = Field(None, description="¿Es monotributista? (SI/NO)")
    factura: Optional[str]                 = Field(None, description="¿Factura? (SI/NO)")
    cobertura: Optional[int]               = Field(None, description="Cobertura")
    vencimiento_cobertura: Optional[date] = Field(None, description="Vencimiento cobertura")
    provincia: Optional[str]               = Field(None, description="Provincia")
    localidad: Optional[str]     = Field(None, description="Localidad")
    codigo_postal: Optional[str]           = Field(None, description="Código postal")
    vitalicio: Optional[str]               = Field(None, description="¿Es vitalicio? (S/N)")
    fecha_vitalicio: Optional[date] = Field(None, description="Fecha de vitalicio")
    observacion: Optional[str]             = Field(None, description="Observaciones")
    categoria: Optional[str]               = Field(None, description="Categoría")
    existe:  Optional[str]            = Field(None, description="¿Existe? (S/N)")
    excep_desde:  Optional[str]            = Field(None, description="Excepción desde (MMYYYY)")
    excep_hasta: Optional[str]            = Field(None, description="Excepción hasta (MMYYYY)")
    excep_desde2: Optional[str]           = Field(None, description="Segunda excepción desde")
    excep_hasta2: Optional[str]           = Field(None, description="Segunda excepción hasta")
    excep_desde3: Optional[str]           = Field(None, description="Tercera excepción desde")
    excep_hasta3: Optional[str]           = Field(None, description="Tercera excepción hasta")
    ingresar:  Optional[str]                = Field(None, description="Campo de control ingreso")
    cbu: Optional[str]           = Field(None, description="CBU")
    nro_resolucion: Optional[str] = Field(None, description="Numero de resolucion")
    fecha_resolucion: Optional[date] = Field(None, description="Fecha de resolucion")
    conceps_espec: Optional[Dict[str, Any]] = Field(None, description="Store conceps/espec")
    attach_titulo: Optional[str] = Field(None, description="Adjunto titulo")
    attach_matricula_nac: Optional[str] = Field(None, description="Adjunto matricula nac")
    attach_matricula_prov: Optional[str] = Field(None, description="Adjunto matricula prov")
    attach_resolucion: Optional[str] = Field(None, description="Adjunto resolucion")
    attach_habilitacion_municipal: Optional[str] = Field(None, description="Adjunto habilitacion municipal")
    attach_cuit: Optional[str] = Field(None, description="Adjunto cuit")
    attach_condicion_impositiva: Optional[str] = Field(None, description="Adjunto condicion impositiva")
    attach_anssal: Optional[str] = Field(None, description="Adjunto anssal")
    attach_malapraxis: Optional[str] = Field(None, description="Adjunto malapraxis")
    attach_cbu: Optional[str] = Field(None, description="Adjunto cbu")
    attach_dni: Optional[str] = Field(None, description="Adjunto dni")

    @field_validator(
        "fecha_recibido", "fecha_matricula", "fecha_ingreso",
        "fecha_nac", "vencimiento_anssal", "vencimiento_malapraxis",
        "vencimiento_cobertura", "fecha_vitalicio", "fecha_resolucion",
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


class MedicoPartialIn(BaseModel):
    # Identificación
    documentType: Optional[str] = None      # DNI, LE, LC, Pasaporte, etc.
    documentNumber: Optional[str] = None
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    gender: Optional[str] = None            # 'M'/'F'/otro
    birthDate: Optional[str] = None         # 'YYYY-MM-DD'

    # Contacto
    phone: Optional[str] = None
    altPhone: Optional[str] = None
    email: Optional[str] = None

    # Domicilio
    address: Optional[str] = None
    addressNumber: Optional[str] = None
    addressFloor: Optional[str] = None
    addressDept: Optional[str] = None
    province: Optional[str] = None
    locality: Optional[str] = None
    postalCode: Optional[str] = None

    # Profesionales
    matriculaProv: Optional[str] = None
    matriculaNac: Optional[str] = None
    joinDate: Optional[str] = None          # 'YYYY-MM-DD'

    # Especialidades (admin puede cargar luego en su pestaña; acá lo dejamos opcional)
    specialties: Optional[List[str]] = None

    # Fiscales / Seguros
    cuit: Optional[str] = None
    taxCondition: Optional[str] = None      # RI/Monotrib/Exento/etc.
    anssal: Optional[str] = None
    anssalExpiry: Optional[str] = None      # 'YYYY-MM-DD'
    malpracticeCompany: Optional[str] = None
    malpracticeExpiry: Optional[str] = None # 'YYYY-MM-DD'
    cbu: Optional[str] = None

    # Otros
    observations: Optional[str] = None

class SaveContinueOut(BaseModel):
    medico_id: int
    ok: bool = True

class ExisteIn(BaseModel):
    existe: Literal["S", "N"]

# class MedicoDetailOut(BaseModel):
#     # Esquemita simple para GET; ajustá si necesitás más campos
#     ID: int = Field(..., alias="id")
#     DOCUMENTO: Optional[str] = None
#     NOMBRE: Optional[str] = None
#     nombre_: Optional[str] = None
#     apellido: Optional[str] = None
#     EMAIL: Optional[str] = None
#     TELEFONO: Optional[str] = None
#     DOMICILIO: Optional[str] = None
#     LOCALIDAD: Optional[str] = None
#     PROVINCIA: Optional[str] = None
#     COD_POSTAL: Optional[str] = None
#     MATRICULA_PROV: Optional[str] = None
#     MATRICULA_NAC: Optional[str] = None
#     FECHA_INGRESO: Optional[str] = None
#     CUIT: Optional[str] = None
#     COND_IMPOSITIVA: Optional[str] = None
#     ANSSAL: Optional[str] = None
#     ANSSAL_VENC: Optional[str] = None
#     MALAPRACTICA_COMPANIA: Optional[str] = None
#     MALAPRACTICA_VENC: Optional[str] = None
#     CBU: Optional[str] = None
#     OBSERVACIONES: Optional[str] = None
#     SEXO: Optional[str] = None
#     FECHA_NAC: Optional[str] = None
#     EXISTE: Optional[str] = None    

class AdminSaveContinueIn(BaseModel):
    medico_id: Optional[int] = None
    # todos los del público, opcionales:
    documentType: Optional[str] = None
    documentNumber: Optional[str] = None
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    gender: Optional[str] = None
    birthDate: Optional[str] = None
    address: Optional[str] = None
    province: Optional[str] = None
    locality: Optional[str] = None
    postalCode: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None
    officeAddress: Optional[str] = None
    officePhone: Optional[str] = None
    cuit: Optional[str] = None
    observations: Optional[str] = None
    anssal: Optional[str] = None
    anssalExpiry: Optional[str] = None
    malpracticeCompany: Optional[str] = None
    malpracticeExpiry: Optional[str] = None
    malpracticeCoverage: Optional[str] = None
    provincialLicense: Optional[str] = None
    provincialLicenseDate: Optional[str] = None
    nationalLicense: Optional[str] = None
    nationalLicenseDate: Optional[str] = None
    graduationDate: Optional[str] = None
    resolutionNumber: Optional[str] = None
    resolutionDate: Optional[str] = None
    specialty: Optional[str] = None
    condicionImpositiva: Optional[str] = None
    taxCondition: Optional[str] = None
    cbu: Optional[str] = None
