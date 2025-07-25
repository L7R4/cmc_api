from pydantic import BaseModel

from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import date
from decimal import Decimal
from datetime import date, datetime

#region  Medico ###
class MedicoBase(BaseModel):
    nro_especialidad: int                    = Field(..., description="Especialidad principal")
    nro_especialidad2: int                   = Field(..., description="Especialidad 2")
    nro_especialidad3: int                   = Field(..., description="Especialidad 3")
    nro_especialidad4: int                   = Field(..., description="Especialidad 4")
    nro_especialidad5: int                   = Field(..., description="Especialidad 5")
    nro_especialidad6: int                   = Field(..., description="Especialidad 6")
    nro_especialidad7: Optional[int]         = Field(None, description="Especialidad 7 (opcional)")
    nro_socio: int                           = Field(..., description="Número de socio")
    nombre: str                              = Field(..., description="Nombre completo")
    domicilio_consulta: str                  = Field(..., description="Domicilio de consulta")
    telefono_consulta: str                   = Field(..., description="Teléfono de consulta")
    matricula_prov: int                      = Field(..., description="Matrícula Provincial")
    matricula_nac: int                       = Field(..., description="Matrícula Nacional")
    fecha_recibido: Optional[date]           = Field(None, description="Fecha recibido expediente")
    fecha_matricula: Optional[date]          = Field(None, description="Fecha de matrícula")
    fecha_ingreso: Optional[date]            = Field(None, description="Fecha de ingreso al colegio")
    domicilio_particular: str                = Field(..., description="Domicilio particular")
    tele_particular: str                     = Field(..., description="Teléfono particular")
    celular_particular: str                  = Field(..., description="Celular particular")
    mail_particular: str                     = Field(..., description="Email particular")
    sexo: str                                = Field(..., description="Sexo (M/F)")
    tipo_doc: str                            = Field(..., description="Tipo de documento")
    documento: str                           = Field(..., description="Número de documento")
    fecha_nac: Optional[date]                = Field(None, description="Fecha de nacimiento")
    cuit: str                                = Field(..., description="CUIT")
    anssal: int                              = Field(..., description="ANS Salud")
    vencimiento_anssal: Optional[date]       = Field(None, description="Vencimiento ANSSAL")
    malapraxis: str                          = Field(..., description="Seguro de mala praxis")
    vencimiento_malapraxis: Optional[date]   = Field(None, description="Vencimiento mala praxis")
    monotributista: str                      = Field(..., description="¿Es monotributista? (SI/NO)")
    factura: str                             = Field(..., description="¿Factura? (SI/NO)")
    cobertura: int                           = Field(..., description="Cobertura")
    vencimiento_cobertura: Optional[date]    = Field(None, description="Vencimiento cobertura")
    provincia: str                           = Field(..., description="Provincia")
    codigo_postal: str                       = Field(..., description="Código postal")
    vitalicio: str                           = Field(..., description="¿Es vitalicio? (S/N)")
    fecha_vitalicio: Optional[date]          = Field(None, description="Fecha de vitalicio")
    observacion: str                         = Field(..., description="Observaciones")
    categoria: str                           = Field(..., description="Categoría")
    existe: str                              = Field(..., description="¿Existe? (S/N)")
    excep_desde: str                         = Field(..., description="Excepción desde (MMYYYY)")
    excep_hasta: str                         = Field(..., description="Excepción hasta (MMYYYY)")
    excep_desde2: str                        = Field(..., description="Segunda excepción desde")
    excep_hasta2: str                        = Field(..., description="Segunda excepción hasta")
    excep_desde3: str                        = Field(..., description="Tercera excepción desde")
    excep_hasta3: str                        = Field(..., description="Tercera excepción hasta")
    ingresar: str                            = Field(..., description="Campo de control ingreso")

    @field_validator(
        "fecha_recibido",
        "fecha_matricula",
        "fecha_ingreso",
        "fecha_nac",
        "vencimiento_anssal",
        "vencimiento_malapraxis",
        "vencimiento_cobertura",
        "fecha_vitalicio",
        mode="before"
    )
    @classmethod
    def _fix_zero_dates(cls, v):
        # si viene '0000-00-00' o un año 0000, lo convertimos a None
        if isinstance(v, str) and (v == "0000-00-00" or v.startswith("0000-")):
            return None
        return v
    
class MedicoCreate(MedicoBase):
    """
    Todos los campos obligatorios para crear un Medico.
    Id no se expone aquí porque lo genera la base.
    """
    pass

class MedicoUpdate(BaseModel):
    """
    Permitimos actualizar cualquier campo de forma parcial.
    """
    nro_especialidad: Optional[int]                  = None
    nro_especialidad2: Optional[int]                 = None
    nro_especialidad3: Optional[int]                 = None
    nro_especialidad4: Optional[int]                 = None
    nro_especialidad5: Optional[int]                 = None
    nro_especialidad6: Optional[int]                 = None
    nro_especialidad7: Optional[int]                 = None
    nro_socio: Optional[int]                         = None
    nombre: Optional[str]                            = None
    domicilio_consulta: Optional[str]                = None
    telefono_consulta: Optional[str]                 = None
    matricula_prov: Optional[int]                    = None
    matricula_nac: Optional[int]                     = None
    fecha_recibido: Optional[date]                   = None
    fecha_matricula: Optional[date]                  = None
    fecha_ingreso: Optional[date]                    = None
    domicilio_particular: Optional[str]              = None
    tele_particular: Optional[str]                   = None
    celular_particular: Optional[str]                = None
    mail_particular: Optional[str]                   = None
    sexo: Optional[str]                              = None
    tipo_doc: Optional[str]                          = None
    documento: Optional[str]                         = None
    fecha_nac: Optional[date]                        = None
    cuit: Optional[str]                              = None
    anssal: Optional[int]                            = None
    vencimiento_anssal: Optional[date]               = None
    malapraxis: Optional[str]                        = None
    vencimiento_malapraxis: Optional[date]           = None
    monotributista: Optional[str]                    = None
    factura: Optional[str]                           = None
    cobertura: Optional[int]                         = None
    vencimiento_cobertura: Optional[date]            = None
    provincia: Optional[str]                         = None
    codigo_postal: Optional[str]                     = None
    vitalicio: Optional[str]                         = None
    fecha_vitalicio: Optional[date]                  = None
    observacion: Optional[str]                       = None
    categoria: Optional[str]                         = None
    existe: Optional[str]                            = None
    excep_desde: Optional[str]                       = None
    excep_hasta: Optional[str]                       = None
    excep_desde2: Optional[str]                      = None
    excep_hasta2: Optional[str]                      = None
    excep_desde3: Optional[str]                      = None
    excep_hasta3: Optional[str]                      = None
    ingresar: Optional[str]                          = None

class MedicoOut(MedicoBase):
    """
    El esquema que devolvemos en las respuestas, incluye el ID.
    """
    id: int = Field(..., description="Id")

    class Config:
        orm_mode = True
#endregion

#region ConceptoDescuento ###
class ConceptoDescuentoBase(BaseModel):
    cod_ref: str   = Field(..., max_length=50)
    nombre: str    = Field(..., max_length=100)
    precio: Decimal= Field(..., ge=0)
    porcentaje: Decimal = Field(..., ge=0, le=100)

class ConceptoDescuentoCreate(ConceptoDescuentoBase): pass

class ConceptoDescuentoUpdate(BaseModel):
    cod_ref:    Optional[str]    = Field(None, max_length=50)
    nombre:     Optional[str]    = Field(None, max_length=100)
    precio:     Optional[Decimal] = None
    porcentaje: Optional[Decimal] = None

class ConceptoDescuentoOut(ConceptoDescuentoBase):
    id: int
    class Config:
        orm_mode = True
#endregion

#region ObraSocial ###
class ObraSocialBase(BaseModel):
    nombre: str = Field(..., max_length=100)

class ObraSocialCreate(ObraSocialBase): pass

class ObraSocialUpdate(BaseModel):
    nombre: Optional[str] = Field(None, max_length=100)

class ObraSocialOut(ObraSocialBase):
    id: int
    class Config:
        orm_mode = True
#endregion

#region Medico ###
class MedicoBase(BaseModel):
    cod_med: int   = Field(..., ge=1)
    nombre: str    = Field(..., max_length=100)
    email: str     = Field(...)
    password: str  = Field(..., min_length=6)

class MedicoCreate(MedicoBase): pass

class MedicoUpdate(BaseModel):
    cod_med: Optional[int]   = None
    nombre:  Optional[str]   = Field(None, max_length=100)
    email:   Optional[str]   = None
    password:Optional[str]   = Field(None, min_length=6)

class MedicoOut(BaseModel):
    id: int
    cod_med: int
    nombre: str
    email: str
    class Config:
        orm_mode = True
#endregion

#region Periodo ###
class PeriodoBase(BaseModel):
    periodo: str    = Field(..., max_length=7)
    version: int    = Field(..., ge=1, description="——— WEPSSS ———")
    status: str     = Field(..., max_length=20)
    total_bruto: Decimal       = Field(..., ge=0)
    total_debitado: Decimal    = Field(..., ge=0)
    total_descontado: Decimal  = Field(..., ge=0)
    total_neto: Decimal        = Field(..., ge=0)

class PeriodoCreate(BaseModel):
    periodo: str = Field(..., max_length=7)

class PeriodoUpdate(BaseModel):
    status: Optional[str]            = Field(None, max_length=20)
    version: Optional[int]           = Field(None, ge=1)
    total_bruto: Optional[Decimal]      = None
    total_debitado: Optional[Decimal]   = None
    total_descontado: Optional[Decimal] = None
    total_neto: Optional[Decimal]       = None

class PeriodoOut(PeriodoBase):
    id: int
    created_at: datetime
    updated_at: datetime
    class Config:
        orm_mode = True
#endregion

#region Prestacion ###
class PrestacionBase(BaseModel):
    periodo_id: int
    id_med: int
    id_os: int
    id_nomenclador: int
    cantidad: int
    honorarios: Decimal
    gastos: Decimal
    ayudante: Decimal
    importe_total: Decimal

class PrestacionCreate(PrestacionBase): pass

class PrestacionUpdate(BaseModel):
    cantidad:      Optional[int]     = None
    honorarios:    Optional[Decimal] = None
    gastos:        Optional[Decimal] = None
    ayudante:      Optional[Decimal] = None
    importe_total: Optional[Decimal] = None

class PrestacionOut(PrestacionBase):
    id: int  = Field(..., alias="id_prestacion")
    created_at: datetime
    class Config:
        orm_mode = True
#endregion

#region Debito ###
class DebitoBase(BaseModel):
    id_med: int
    id_os: int
    periodo_id: int
    id_prestacion: int
    importe: Decimal
    observaciones: Optional[str]

class DebitoCreate(DebitoBase): pass

class DebitoUpdate(BaseModel):
    importe:      Optional[Decimal] = None
    observaciones: Optional[str]    = None

class DebitoOut(DebitoBase):
    id: int
    created_at: datetime
    class Config:
        orm_mode = True

#endregion

#region Descuento ###
class DescuentoBase(BaseModel):
    id_lista_concepto_descuento: int
    id_med: int
    periodo_id: int

class DescuentoCreate(DescuentoBase): pass

class DescuentoUpdate(BaseModel):
    # ninguna actualización típica salvo quizás el concepto
    id_lista_concepto_descuento: Optional[int] = None

class DescuentoOut(DescuentoBase):
    id: int
    created_at: datetime
    class Config:
        orm_mode = True
#endregion


#region Detalle facturacion schema definitions
# class DetalleFacturacionBase(BaseModel):
#     periodo: str = Field(..., max_length=6, description="Periodo (YYYYMM o similar)")
#     cod_med: int = Field(..., description="Código de médico (bigint)")
#     categoria: str = Field(..., min_length=1, max_length=1, description="Categoría (char(1))")
#     id_especialidad: int = Field(..., description="ID de especialidad (smallint)")
#     nro_orden: int = Field(..., description="Número de orden (bigint)")
#     cod_obr: int = Field(..., description="Código OB (smallint)")
#     cod_nom: str = Field(..., max_length=8, description="Código nombre (varchar(8))")
#     tpo_funcion: str = Field(..., max_length=2, description="Tipo función (varchar(2))")
#     sesion: int = Field(..., description="Sesiones (int)")
#     cantidad: int = Field(..., description="Cantidad (int)")
#     dni_p: str = Field(..., max_length=20, description="DNI / OSDE nro afiliado (varchar(20))")
#     nom_ape_p: str = Field(..., max_length=60, description="Nombre y apellido paciente (varchar(60))")
#     tpo_serv: str = Field(..., min_length=1, max_length=1, description="Tipo de servicio (char(1))")
#     cod_clinica: int = Field(..., description="Código clínica (smallint)")
#     fecha_practica: date = Field(..., description="Fecha de práctica (date)")
#     tipo_orden: str = Field(..., min_length=1, max_length=1, description="Tipo de orden (char(1))")
#     porc: int = Field(..., description="Porcentaje (int)")
#     honorarios: Decimal = Field(..., description="Honorarios (double(10,2))")
#     gastos: Decimal = Field(..., description="Gastos (double(10,2))")
#     ayudante: Decimal = Field(..., description="Ayudante (double(10,2))")
#     importe_total: Decimal = Field(..., description="Importe total (double(10,2))")
#     manual: str = Field(..., min_length=1, max_length=1, description="Manual (char(1))")
#     cod_med_indica: str = Field(..., max_length=12, description="Código médico indica (varchar(12))")
#     codigo_oms: str = Field(..., max_length=12, description="Código OMS (varchar(12))")
#     diag: str = Field(..., max_length=100, description="Diagnóstico / transacción OSDE (varchar(100))")
#     nro_vias: str = Field(..., max_length=2, description="Número de vías (varchar(2))")
#     fin_semana: str = Field(..., max_length=6, description="Fin de semana / plan OSDE (varchar(6))")
#     nocturno: str = Field(..., max_length=2, description="Nocturno / tipo prestación OSDE (varchar(2))")
#     feriado: str = Field(..., min_length=2, max_length=2, description="Nomenclador: NN, CA, CI, NC (char(2))")
#     urgencia: str = Field(..., min_length=1, max_length=1, description="Urgencia (char(1))")
#     estado: str = Field("A", min_length=1, max_length=1, description="Estado (char(1))")
#     usuario: str = Field("", max_length=15, description="Usuario creador (varchar(15))")
#     ck_practica_id: Optional[int] = Field(None, description="CK práctica (int)")
#     ck_revisar:     Optional[int] = Field(None, description="CK revisar (int)")
#     ck_estado_id:   Optional[int] = Field(None, description="CK estado (int)")

# class DetalleFacturacionCreate(DetalleFacturacionBase):
#     """
#     Todos los campos obligatorios para crear un DetalleFacturacion.
#     El ID y created se asignan automáticamente en la base de datos.
#     """
#     pass

# class DetalleFacturacionUpdate(BaseModel):
#     """
#     Cualquier campo puede enviarse de forma parcial para actualizar.
#     """
#     periodo: Optional[str]            = Field(None, max_length=6)
#     cod_med: Optional[int]            = None
#     categoria: Optional[str]          = Field(None, min_length=1, max_length=1)
#     id_especialidad: Optional[int]    = None
#     nro_orden: Optional[int]          = None
#     cod_obr: Optional[int]            = None
#     cod_nom: Optional[str]            = Field(None, max_length=8)
#     tpo_funcion: Optional[str]        = Field(None, max_length=2)
#     sesion: Optional[int]             = None
#     cantidad: Optional[int]           = None
#     dni_p: Optional[str]              = Field(None, max_length=20)
#     nom_ape_p: Optional[str]          = Field(None, max_length=60)
#     tpo_serv: Optional[str]           = Field(None, min_length=1, max_length=1)
#     cod_clinica: Optional[int]        = None
#     fecha_practica: Optional[date]    = None
#     tipo_orden: Optional[str]         = Field(None, min_length=1, max_length=1)
#     porc: Optional[int]               = None
#     honorarios: Optional[Decimal]     = None
#     gastos: Optional[Decimal]         = None
#     ayudante: Optional[Decimal]       = None
#     importe_total: Optional[Decimal]  = None
#     manual: Optional[str]             = Field(None, min_length=1, max_length=1)
#     cod_med_indica: Optional[str]     = Field(None, max_length=12)
#     codigo_oms: Optional[str]         = Field(None, max_length=12)
#     diag: Optional[str]               = Field(None, max_length=100)
#     nro_vias: Optional[str]           = Field(None, max_length=2)
#     fin_semana: Optional[str]         = Field(None, max_length=6)
#     nocturno: Optional[str]           = Field(None, max_length=2)
#     feriado: Optional[str]            = Field(None, min_length=2, max_length=2)
#     urgencia: Optional[str]           = Field(None, min_length=1, max_length=1)
#     estado: Optional[str]             = Field(None, min_length=1, max_length=1)
#     usuario: Optional[str]            = Field(None, max_length=15)
#     ck_practica_id: Optional[int]     = None
#     ck_revisar:     Optional[int]     = None
#     ck_estado_id:   Optional[int]     = None

# class DetalleFacturacionOut(DetalleFacturacionBase):
#     """
#     Lo que devolvemos en las respuestas: incluye ID y timestamp.
#     """
#     id_detalle_prestaciones: int       = Field(..., description="PK autoincremental")
#     created: datetime                  = Field(..., description="Timestamp de creación")

#     class Config:
#         orm_mode = True
#endregion
