from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# User-facing helpers (used in medicos routes)
# ---------------------------------------------------------------------------

FIELD_MAP = {
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
    "nro_socio": "NRO_SOCIO",
    "categoria": "categoria",
    "titulo": "titulo",
    "matricula_prov": "MATRICULA_PROV",
    "matricula_nac": "MATRICULA_NAC",
    "fecha_recibido": "FECHA_RECIBIDO",
    "fecha_matricula": "FECHA_MATRICULA",
    "domicilio_consulta": "DOMICILIO_CONSULTA",
    "telefono_consulta": "TELEFONO_CONSULTA",
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
    "fecha_nac", "fecha_recibido", "fecha_matricula", "fecha_resolucion",
    "vencimiento_anssal", "vencimiento_malapraxis", "vencimiento_cobertura",
}


def _coerce_existe(v):
    if v is None:
        return None
    s = str(v).strip().upper()
    if s in ("S", "N"):
        return s
    return "S" if s in ("1", "TRUE", "T", "SI", "SÍ", "Y", "YES", "ON") else "N"


def _coerce_sexo(v):
    if v is None:
        return None
    s = str(v).strip().upper()
    return s[:1] if s else None


def _none_if_dashish(v: Any):
    if isinstance(v, str) and v.strip() in {"—", "-", "N/A", "n/a"}:
        return None
    return v


def _none_if_empty(v: Any):
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


def _date_ymd_or_none(v: Any):
    v = _none_if_empty(v)
    if v is None:
        return None
    if isinstance(v, str) and len(v) == 10 and v[4] == "-" and v[7] == "-":
        return v
    return v


# ---------------------------------------------------------------------------
# User-facing schemas
# ---------------------------------------------------------------------------

class UserOut(BaseModel):
    id: int
    nro_socio: int
    nombre: Optional[str] = None
    scopes: List[str] = []
    role: Optional[str] = None

class UserEnvelope(BaseModel):
    user: UserOut

class MedicoBase(BaseModel):
    id: Optional[int] = Field(None)
    nro_especialidad: Optional[int] = Field(None)
    nro_especialidad2: Optional[int] = Field(None)
    nro_especialidad3: Optional[int] = Field(None)
    nro_especialidad4: Optional[int] = Field(None)
    nro_especialidad5: Optional[int] = Field(None)
    nro_especialidad6: Optional[int] = Field(None)
    nro_socio: Optional[int] = Field(None)
    nombre: Optional[str] = Field(None)
    nombre_: Optional[str] = Field(None)
    apellido: Optional[str] = Field(None)
    domicilio_consulta: Optional[str] = Field(None)
    telefono_consulta: Optional[str] = Field(None)
    matricula_prov: Optional[int] = Field(None)
    matricula_nac: Optional[int] = Field(None)
    fecha_recibido: Optional[date] = Field(None)
    fecha_matricula: Optional[date] = Field(None)
    fecha_ingreso: Optional[date] = Field(None)
    domicilio_particular: Optional[str] = Field(None)
    tele_particular: Optional[str] = Field(None)
    celular_particular: Optional[str] = Field(None)
    mail_particular: Optional[str] = Field(None)
    sexo: Optional[str] = Field(None)
    tipo_doc: Optional[str] = Field(None)
    documento: Optional[str] = Field(None)
    fecha_nac: Optional[date] = Field(None)
    cuit: Optional[str] = Field(None)
    condicion_impositiva: Optional[str] = Field(None)
    anssal: Optional[int] = Field(None)
    vencimiento_anssal: Optional[date] = Field(None)
    malapraxis: Optional[str] = Field(None)
    vencimiento_malapraxis: Optional[date] = Field(None)
    monotributista: Optional[str] = Field(None)
    factura: Optional[str] = Field(None)
    cobertura: Optional[int] = Field(None)
    vencimiento_cobertura: Optional[date] = Field(None)
    provincia: Optional[str] = Field(None)
    localidad: Optional[str] = Field(None)
    codigo_postal: Optional[str] = Field(None)
    vitalicio: Optional[str] = Field(None)
    fecha_vitalicio: Optional[date] = Field(None)
    observacion: Optional[str] = Field(None)
    categoria: Optional[str] = Field(None)
    existe: Optional[str] = Field(None)
    excep_desde: Optional[str] = Field(None)
    excep_hasta: Optional[str] = Field(None)
    excep_desde2: Optional[str] = Field(None)
    excep_hasta2: Optional[str] = Field(None)
    excep_desde3: Optional[str] = Field(None)
    excep_hasta3: Optional[str] = Field(None)
    ingresar: Optional[str] = Field(None)
    cbu: Optional[str] = Field(None)
    nro_resolucion: Optional[str] = Field(None)
    fecha_resolucion: Optional[date] = Field(None)
    es_organizacion: Optional[bool] = Field(None)
    adherente: Optional[bool] = Field(None)
    interior: Optional[bool] = Field(None)
    conceps_espec: Optional[Dict[str, Any]] = Field(None)
    attach_titulo: Optional[str] = Field(None)
    attach_matricula_nac: Optional[str] = Field(None)
    attach_matricula_prov: Optional[str] = Field(None)
    attach_resolucion: Optional[str] = Field(None)
    attach_habilitacion_municipal: Optional[str] = Field(None)
    attach_cuit: Optional[str] = Field(None)
    attach_condicion_impositiva: Optional[str] = Field(None)
    attach_anssal: Optional[str] = Field(None)
    attach_malapraxis: Optional[str] = Field(None)
    attach_cbu: Optional[str] = Field(None)
    attach_dni: Optional[str] = Field(None)
    titulo: Optional[str] = Field(None)

    @field_validator(
        "fecha_recibido", "fecha_matricula", "fecha_ingreso",
        "fecha_nac", "vencimiento_anssal", "vencimiento_malapraxis",
        "vencimiento_cobertura", "fecha_vitalicio", "fecha_resolucion",
        mode="before",
    )
    @classmethod
    def _fix_zero_dates(cls, v):
        if isinstance(v, str) and (v == "0000-00-00" or v.startswith("0000-")):
            return None
        return v


class MedicoListRow(BaseModel):
    id: int
    nro_socio: Optional[int] = None
    nombre: str
    matricula_prov: Optional[int] = None
    mail_particular: Optional[str] = None
    tele_particular: Optional[str] = None
    fecha_ingreso: Optional[str] = None
    documento: Optional[str] = None
    activo: bool = False
    existe: Optional[str] = None

    @field_validator("nro_socio", mode="before")
    @classmethod
    def _coerce_nro_socio(cls, v):
        if v is None:
            return None
        if isinstance(v, (bytes, bytearray, memoryview)):
            v = bytes(v).decode("utf-8", errors="ignore")
        s = str(v).strip()
        if s in ("", "0"):
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
    localidad: Optional[str] = None
    domicilio_particular: Optional[str] = None
    tele_particular: Optional[str] = None
    celular_particular: Optional[str] = None
    titulo: Optional[str] = None
    fecha_recibido: Optional[date] = None
    fecha_matricula: Optional[date] = None
    fecha_ingreso: Optional[date] = None
    nro_resolucion: Optional[str] = None
    fecha_resolucion: Optional[date] = None
    especialidades: List[EspecialidadOut] = []
    condicion_impositiva: Optional[str] = None
    anssal: Optional[int] = None
    vencimiento_anssal: Optional[date] = None
    malapraxis: Optional[str] = None
    vencimiento_malapraxis: Optional[date] = None
    cobertura: Optional[int] = None
    vencimiento_cobertura: Optional[date] = None
    cbu: Optional[str] = None
    observacion: Optional[str] = None
    es_organizacion: Optional[bool] = None
    adherente: Optional[bool] = None
    interior: Optional[bool] = None
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
        if s in ("", "0", "@"):
            return None
        return s

    @field_validator(
        "fecha_recibido", "fecha_matricula", "fecha_ingreso", "fecha_nac",
        "vencimiento_anssal", "vencimiento_malapraxis", "vencimiento_cobertura",
        mode="before",
    )
    @classmethod
    def _fix_zero_dates(cls, v):
        if isinstance(v, str) and (v == "0000-00-00" or v.startswith("0000-")):
            return None
        return v


class MedicoDebtOut(BaseModel):
    has_debt: bool = False
    amount: Decimal = Decimal("0")
    last_invoice: Optional[str] = None
    since: Optional[str] = None


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
    es_organizacion: Optional[bool] = None
    adherente: Optional[bool] = None
    interior: Optional[bool] = None

    model_config = ConfigDict(extra="ignore")

    @field_validator(
        "name", "nombre_", "apellido", "sexo", "documento", "cuit", "existe", "provincia",
        "localidad", "codigo_postal", "domicilio_particular", "tele_particular",
        "celular_particular", "mail_particular", "categoria", "titulo",
        "domicilio_consulta", "telefono_consulta", "condicion_impositiva",
        "malapraxis", "cbu", "observacion",
        mode="before",
    )
    @classmethod
    def _blank_dash_to_none(cls, v):
        v = _none_if_empty(v)
        v = _none_if_dashish(v)
        return v

    @field_validator(
        "fecha_nac", "fecha_recibido", "fecha_matricula",
        "vencimiento_anssal", "vencimiento_malapraxis", "vencimiento_cobertura",
        mode="before",
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


class ConceptoAplicacionOut(BaseModel):
    resumen_id: int
    periodo: str
    created_at: Optional[datetime] = None
    monto_aplicado: Decimal
    porcentaje_aplicado: Decimal


class MedicoConceptoOut(BaseModel):
    concepto_tipo: Literal["desc", "esp"]
    concepto_id: int
    concepto_nro_colegio: Optional[int] = None
    concepto_nombre: Optional[str] = None
    saldo: Decimal
    aplicaciones: List[ConceptoAplicacionOut] = []


class MedicoEspecialidadOut(BaseModel):
    id: int
    nombre: Optional[str] = None
    n_resolucion: Optional[str] = None
    fecha_resolucion: Optional[str] = None
    adjunto_id: Optional[int] = None
    adjunto_url: Optional[str] = None


class MedicoDocOut(BaseModel):
    id: int
    label: str
    pretty_label: str
    file_name: str
    url: str
    content_type: Optional[str] = None
    size: Optional[int] = None


class AsignarEspecialidadIn(BaseModel):
    op: Literal["add", "remove", "update"] = "add"
    id_colegio: int
    n_resolucion: Optional[str] = None
    fecha_resolucion: Optional[str] = None
    adjunto_id: Optional[int] = None


class AsociarConceptoIn(BaseModel):
    concepto_tipo: Literal["desc", "esp"]
    concepto_id: int


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
    concepto_id: int
    op: Literal["add", "remove"] = "add"


class DoctorStatsPointOut(BaseModel):
    month: str
    consultas: int
    facturado: float
    obras: Dict[str, float] = Field(default_factory=dict)


class MedicoPartialIn(BaseModel):
    documentType: Optional[str] = None
    documentNumber: Optional[str] = None
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    gender: Optional[str] = None
    birthDate: Optional[str] = None
    phone: Optional[str] = None
    altPhone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    addressNumber: Optional[str] = None
    addressFloor: Optional[str] = None
    addressDept: Optional[str] = None
    province: Optional[str] = None
    locality: Optional[str] = None
    postalCode: Optional[str] = None
    matriculaProv: Optional[str] = None
    matriculaNac: Optional[str] = None
    joinDate: Optional[str] = None
    specialties: Optional[List[str]] = None
    cuit: Optional[str] = None
    taxCondition: Optional[str] = None
    anssal: Optional[str] = None
    anssalExpiry: Optional[str] = None
    malpracticeCompany: Optional[str] = None
    malpracticeExpiry: Optional[str] = None
    cbu: Optional[str] = None
    observations: Optional[str] = None


class SaveContinueOut(BaseModel):
    medico_id: int
    ok: bool = True


class ExisteIn(BaseModel):
    existe: Literal["S", "N"]


class AdminSaveContinueIn(BaseModel):
    medico_id: Optional[int] = None
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


# ---------------------------------------------------------------------------
# Registration schemas (from registro_schema.py)
# ---------------------------------------------------------------------------

class SpecialtyItemIn(BaseModel):
    id_colegio_espe: int = Field(..., description="ID_COLEGIO_ESPE de la especialidad")
    n_resolucion: Optional[str] = None
    fecha_resolucion: Optional[str] = None
    adjunto: Optional[str] = None


class RegisterIn(BaseModel):
    documentType: str
    documentNumber: str
    firstName: str
    lastName: str
    email: str | None = None
    phone: str | None = None
    mobile: str | None = None
    address: str | None = None
    gender: Optional[str] = None
    province: str | None = None
    locality: str | None = None
    postalCode: str | None = None
    officeAddress: str | None = None
    officePhone: str | None = None
    cuit: str | None = None
    cbu: str | None = None
    condicionImpositiva: str | None = None
    observations: str | None = None
    provincialLicense: str | None = None
    title: str | None = None
    nationalLicense: str | None = None
    graduationDate: str | None = None
    specialty: str | None = None
    resolutionNumber: str | None = None
    provincialLicenseDate: str | None = None
    nationalLicenseDate: str | None = None
    resolutionDate: str | None = None
    birthDate: str | None = None
    anssal: int | None = None
    anssalExpiry: str | None = None
    malpracticeCompany: str | None = None
    malpracticeExpiry: str | None = None
    malpracticeCoverage: str | None = None
    coverageExpiry: str | None = None
    taxCondition: str | None = None
    specialties: Optional[List[SpecialtyItemIn]] = None

    @field_validator("gender")
    @classmethod
    def norm_gender(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        s = str(v).strip().upper()
        if s in ("M", "F"):
            return s
        if s.startswith("MASC"):
            return "M"
        if s.startswith("FEM"):
            return "F"
        return s[:1]


class RegisterOut(BaseModel):
    medico_id: int
    solicitud_id: int
    ok: bool = True


# ---------------------------------------------------------------------------
# Deuda schemas (from deduccion_schema.py)
# ---------------------------------------------------------------------------

class InstallmentIn(BaseModel):
    n: int
    dueDate: str
    amount: Decimal


class NuevaDeudaIn(BaseModel):
    concept: str = Field(..., description="Texto libre")
    amount: Decimal | None = None
    mode: Literal["full", "installments"] = "full"
    installments: Optional[List[InstallmentIn]] = None


class CrearDeudaOut(BaseModel):
    created: bool
    added_amount: Decimal
    saldo_total: Decimal
