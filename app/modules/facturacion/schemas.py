import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.medicos.schemas import MedicoEspecialidadOut

# ── Tipos literales ──────────────────────────────────────────────────────────
TipoCalculo = Literal["A", "M"]


# ── Autocomplete de médicos ───────────────────────────────────────────────────
class MedicoBuscarOut(BaseModel):
    cod: str
    nombre: str
    matricula: Optional[int] = None
    categoria: Optional[str] = None
    condicion_impositiva: Optional[str] = None
    # `/medicos` devuelve médicos Y clínicas (misma tabla listado_medico). Este flag
    # permite al front distinguirlos en un solo pedido: True → es una clínica/organización
    # (payee que cobra, requiere médico ejecutor); False → médico real.
    es_organizacion: bool = False
    especialidades: list[MedicoEspecialidadOut] = Field(default_factory=list)


# ── Autocomplete de clínicas/organizaciones ───────────────────────────────────
class ClinicaBuscarOut(BaseModel):
    """Misma tabla `listado_medico` que los médicos, filtrada por
    `es_organizacion=1`. `cod` = NRO_SOCIO — se envía en `PrestacionItem.cod_medico`
    (junto con el médico en `cod_medico_ejecutor`) y el backend lo baja a `cod_clinica`."""
    cod: int
    nombre: str
    documento: Optional[str] = None
    cuit: Optional[str] = None
    localidad: Optional[str] = None


class EspecialidadSimpleOut(BaseModel):
    id: int
    nombre: Optional[str] = None


class CodigoHabilitadoOut(BaseModel):
    codigo: str
    descripcion: str
    categoria: Optional[str] = None
    complejidad: Optional[str] = None
    # Especialidad(es) DEL MÉDICO que habilitan este código — vacía si entra por
    # excepción individual o por "sin restricción de especialidad" (no por especialidad).
    especialidades: list[EspecialidadSimpleOut] = Field(default_factory=list)

    class Config:
        from_attributes = True


# ── Afiliados ────────────────────────────────────────────────────────────────
class AfiliadoCreate(BaseModel):
    # El campo se sigue llamando `dni` por compatibilidad, pero identifica al paciente
    # por DNI **o** por nro de afiliado de la obra social, que es alfanumérico y puede
    # llevar separadores (ej. "1231233/00"). Por eso no se valida como sólo dígitos.
    dni: str = Field(..., min_length=4, max_length=20, pattern=r"^[A-Za-z0-9./\-]+$")
    nombre: str = Field(..., min_length=1, max_length=100)

    @field_validator("dni", mode="before")
    @classmethod
    def _limpiar(cls, v):
        # Espacios accidentales del pegado/tipeo del operador; el resto se conserva tal
        # cual (no se normalizan separadores para no crear duplicados del mismo padrón).
        return v.strip() if isinstance(v, str) else v


class AfiliadoRead(BaseModel):
    id: int
    dni: str
    nombre: str
    usuario: str
    created_at: datetime.datetime

    class Config:
        from_attributes = True


# ── Prestaciones — request ───────────────────────────────────────────────────
class PrestacionItem(BaseModel):
    """Un prestador individual (cirujano o ayudante).

    `periodo` no se envía: lo asigna el backend. El nombre del paciente tampoco: se
    obtiene del padrón `afiliado` por `dni_paciente`.
    """
    # PRESTADOR seleccionado en el formulario. NRO_SOCIO de un médico o de una clínica
    # (listado_medico con es_organizacion=1, vía autocomplete /clinicas). NO es
    # necesariamente lo que se guarda en `detalle_facturacion.cod_med`: si es una
    # clínica, la clínica baja a `cod_clinica` y el que queda en `cod_med` es el
    # ejecutor — el médico es siempre quien cobra. Ver `service.resolver_prestador`.
    cod_medico: str

    # Médico EJECUTOR — obligatorio cuando `cod_medico` es una clínica. Es el que
    # termina persistido en `cod_med`: cobra Y determina el precio (por su especialidad).
    # Se ignora (o debe repetir a `cod_medico`) cuando el prestador ya es un médico.
    # La columna `cod_med_ejecutor` NO se escribe: este campo es sólo la señal de entrada.
    cod_medico_ejecutor: Optional[str] = None

    # Paciente — solo DNI; el nombre se resuelve contra el padrón afiliado
    dni_paciente: Optional[str] = None

    # Servicio
    # Opcional: en cargas por `cantidad` (equipo/lote) el operador no puede asignarle
    # una fecha distinta a cada unidad. Si no viene, se guarda NULL y el precio se
    # cotiza con la fecha de HOY (el valor vigente más actualizado), ver
    # `service.fecha_para_precio`.
    fecha_practica: Optional[datetime.date] = None
    # Clínica donde se hizo la prestación, cuando `cod_medico` es un MÉDICO que factura
    # por sí mismo (la clínica es sólo el ámbito). Fuerza `tipo="Honorarios individuales"`
    # — no es un sanatorio, aunque comparta la marca `tipo_orden='S'`. Debe ser una
    # organización (`es_organizacion=1`) o se rechaza con 422.
    # Si `cod_medico` ya es una clínica (caso sanatorio), este campo se IGNORA: la
    # clínica se toma de `cod_medico`.
    cod_clinica: Optional[int] = None
    autorizacion: Optional[str] = Field(None, max_length=30)  # nro de autorización de la OS

    # Prestación
    cod_nomenclador: str
    # Vía quirúrgica: T = tradicional (default), L = laparoscópica. Solo afecta el
    # precio de códigos cuyo Honorarios use un galeno de cirugía adulto/infantil
    # (ver app/modules/nomenclador/service_vias.py); en modo manual es solo dato.
    via: Optional[Literal["T", "L"]] = None
    cantidad: int = Field(1, ge=1)
    sesion: int = Field(1, ge=1)

    # Montos — el concepto en >0 es el que se factura (médico/gastos/ayudante implícito).
    # Automático: el backend toma el valor del lookup para cada concepto marcado en >0.
    # Manual: se guardan los montos enviados tal cual.
    # Excepción: bajo clínica (tipo_orden='S'), código de categoría 'Honorarios
    # individuales' y `tipo_calculo="A"`, `gastos` se fuerza a 0 — los factura la clínica.
    tipo_calculo: TipoCalculo = "A"
    honorarios: Optional[Decimal] = None
    gastos: Optional[Decimal] = None
    ayudante: Optional[Decimal] = None
    porcentaje: int = Field(100, ge=1, le=100)

    # Vínculo a la fila del médico (cabeza del equipo) cuando el ayudante se carga aparte.
    grupo_equipo_id: Optional[int] = None


class PrestacionesCreate(BaseModel):
    """Payload del POST /facturacion/prestaciones.

    1 ítem = carga individual. N ítems = equipo quirúrgico (misma transacción).
    """
    obra_social: str                      # cod_obra_social
    # Período destino (YYYYMM) — SOLO carga del colegio. None = automático (último
    # período cerrado + 1). Se envía cuando el operador usa "editar período" para saltar
    # meses sin movimiento (ej. última cerrada abril, sin mayo → cargar directo junio).
    # Debe ser >= el automático; un período ya cerrado se rechaza (usar complemento).
    # En la carga del médico este campo se ignora (el período sale del puntero).
    periodo: Optional[str] = Field(None, pattern=r"^\d{6}$")
    prestaciones: list[PrestacionItem] = Field(..., min_length=1)


class PrestacionesComplementariaCreate(BaseModel):
    """Payload de POST /facturacion/prestaciones-complementaria.

    A diferencia de `PrestacionesCreate`, la cabecera (factura complementaria) YA
    existe — se creó explícitamente con `POST /facturas/complemento` y se referencia
    acá por su `factura_id` (`id_prestaciones`), no se infiere por obra_social+período.
    `obra_social` y `periodo` se toman de la factura, no se envían.
    """
    factura_id: int
    prestaciones: list[PrestacionItem] = Field(..., min_length=1)


class PrestacionUpdate(BaseModel):
    """PATCH — todos los campos opcionales. No se puede cambiar periodo, cod_obra
    ni nro_orden. Si se envía dni_paciente, se relee el nombre del padrón."""
    # Mismo trío que en la carga: `cod_medico` es el prestador seleccionado (médico o
    # clínica), `cod_medico_ejecutor` el médico cuando el prestador es una clínica, y
    # `cod_clinica` el ámbito cuando el prestador es un médico. El backend los reparte en
    # cod_med / cod_clinica / tipo_orden / tipo. Los campos que no se envían se toman de
    # la fila (se reconstruye la selección original y se revalida). Enviar
    # `cod_clinica: null` explícito saca la clínica.
    cod_medico: Optional[str] = None
    cod_medico_ejecutor: Optional[str] = None
    cod_clinica: Optional[int] = None
    dni_paciente: Optional[str] = None
    fecha_practica: Optional[datetime.date] = None
    autorizacion: Optional[str] = Field(None, max_length=30)
    cod_nomenclador: Optional[str] = None
    via: Optional[Literal["T", "L"]] = None
    cantidad: Optional[int] = Field(None, ge=1)
    sesion: Optional[int] = Field(None, ge=1)
    tipo_calculo: Optional[TipoCalculo] = None
    honorarios: Optional[Decimal] = None
    gastos: Optional[Decimal] = None
    ayudante: Optional[Decimal] = None
    porcentaje: Optional[int] = Field(None, ge=1, le=100)
    grupo_equipo_id: Optional[int] = None


class PrestacionesRevisadoUpdate(BaseModel):
    """PATCH /prestaciones/revisado — actualización batch del checkbox de auditoría."""
    marcados: list[int] = Field(default_factory=list)
    desmarcados: list[int] = Field(default_factory=list)

    @field_validator("marcados", "desmarcados")
    @classmethod
    def ids_positivos(cls, ids: list[int]) -> list[int]:
        if any(prestacion_id <= 0 for prestacion_id in ids):
            raise ValueError("Los IDs de prestación deben ser positivos")
        return ids

    @model_validator(mode="after")
    def grupos_validos(self):
        marcados = set(self.marcados)
        desmarcados = set(self.desmarcados)
        if not marcados and not desmarcados:
            raise ValueError("Debe enviar al menos una prestación marcada o desmarcada")
        if len(marcados) != len(self.marcados) or len(desmarcados) != len(self.desmarcados):
            raise ValueError("No se permiten IDs repetidos")
        if marcados & desmarcados:
            raise ValueError("Un ID no puede estar marcado y desmarcado a la vez")
        return self


class MoverPeriodoPayload(BaseModel):
    """Mueve un conjunto de prestaciones al período siguiente o anterior.
    Todas deben pertenecer a la misma OS y período origen."""
    cod_obra: str
    periodo_origen: str               # "YYYYMM"
    ids: list[int] = Field(..., min_length=1)   # id_detalle_prestaciones (PK)
    direccion: Literal["siguiente", "anterior"]


# ── Respuestas ───────────────────────────────────────────────────────────────
class PeriodoActivoResponse(BaseModel):
    cod_obra: str
    periodo: str        # "YYYYMM"
    periodo_label: str  # "Mayo 2026"
    # Versión de la factura de ese período. 1 = fresca/original (siempre el caso en
    # /periodo-activo y /periodo-medico); > 1 en /periodo-colegio si el período
    # devuelto corresponde a un complemento abierto en vez de una cabecera nueva.
    version: int = 1
    es_complemento: bool = False


class PrecioResponse(BaseModel):
    honorarios: Decimal
    gastos: Decimal
    ayudante: Decimal
    descripcion: str
    fuente: str                        # "nm_historial_precio_codigo"
    complejidad: Optional[str] = None  # informativo
    nivel: Optional[int] = None        # informativo
    snapshot: Optional[list[dict]] = None  # componentes del lookup
    admitido: bool
    motivo: Optional[str] = None       # si admitido=False
    # True → código por presupuesto: H/G/A vienen en 0; el monto lo carga el
    # operador a mano (lo informa la OS). Ver _montos_de_item.
    por_presupuesto: bool = False
    # Máximo de ayudantes admitidos para este código+OS (informativo, para que el front
    # limite el armado del equipo). NULL/0 = no lleva ayudantes.
    cantidad_ayudantes: Optional[int] = None
    # Vía cotizada (T=tradicional, L=laparoscópica) y, si L, el nivel de galeno
    # efectivamente usado (ver app/modules/nomenclador/service_vias.py).
    via: str = "T"
    nivel_cotizado: Optional[int] = None


class PrestacionRead(BaseModel):
    id: int = Field(..., alias="id_detalle_prestaciones")
    periodo: str
    # El MÉDICO que cobra — siempre un médico real, aun bajo clínica (la clínica va en
    # `cod_clinica`). Para precargar el formulario de edición: si viene `cod_clinica`,
    # el selector de prestador va con la clínica y éste es el "médico ejecutor".
    cod_medico: str = Field(..., alias="cod_med")
    # LEGACY: sólo tiene valor en filas cargadas antes del 2026-07-31. Hoy el ejecutor
    # es `cod_medico` y esta columna queda NULL.
    cod_medico_ejecutor: Optional[str] = Field(None, alias="cod_med_ejecutor")
    # Legacy, co-propiedad con CMC: igual al PK (id_detalle_prestaciones) para las
    # cargas nuevas — ya no es un identificador propio, se mantiene solo porque
    # liquidación (nro_orden_cmc) y lotes todavía lo leen para mostrar.
    nro_orden: Optional[str] = None
    cod_obra_social: Optional[str] = Field(None, alias="cod_obr")
    # Resuelto en batch contra `obras_sociales` (NRO_OBRASOCIAL) — no viene del ORM.
    nombre_obra_social: Optional[str] = None
    cod_nomenclador: Optional[str] = Field(None, alias="cod_nom")
    via: Optional[str] = None
    tipo: Optional[str] = None
    # Badge "Medico" | "Ayudante" | "Gastos" según qué monto está en >0 (misma derivación
    # que en `GET /facturas/{id}/detalle`, ver `_derivar_tipo_prestador`). NO viene del
    # ORM: se calcula en `obtener_prestacion` — en el resto de las respuestas que usan
    # `PrestacionRead` queda None. Identifica cuál integrante de `grupo` es la cabecera
    # ("Medico") sin tener que interpretar el código legacy `tpo_funcion` (H/HG/G/A).
    tipo_prestador: Optional[str] = None
    grupo_equipo_id: Optional[int] = None
    sesion: Optional[int] = None
    cantidad: Optional[int] = None
    honorarios: Optional[Decimal] = None
    gastos: Optional[Decimal] = None
    ayudante: Optional[Decimal] = None
    importe_total: Optional[Decimal] = None
    estado: Optional[str] = None
    origen_carga: Optional[str] = None  # 'medico' | 'colegio'
    fecha_practica: Optional[datetime.date] = None
    dni_paciente: Optional[str] = Field(None, alias="dni_p")
    nombre_paciente: Optional[str] = Field(None, alias="nom_ape_p")
    revisado: bool = False
    autorizacion: Optional[str] = None
    # Campos necesarios para precargar el formulario de edición (no se usaban en el
    # listado hasta ahora, pero ya se guardan al crear/editar la prestación).
    # Clínica bajo la que se ejecutó (NRO_SOCIO de la organización); None = el médico
    # factura por sí mismo. `nombre_clinica` se resuelve en batch, no viene del ORM.
    cod_clinica: Optional[int] = None
    nombre_clinica: Optional[str] = None
    # Marca legacy: 'S' si hubo clínica (sea prestador o ámbito), None si no. NO sirve
    # para distinguir sanatorio de ámbito — para eso está `tipo`.
    tipo_orden: Optional[str] = None
    tipo_calculo: Optional[str] = Field(None, alias="manual")   # "A" | "M"
    porcentaje: Optional[int] = Field(None, alias="porc")
    # Integrantes del equipo (mismo `grupo_equipo_id`) DISTINTOS de esta prestación —
    # ayudantes / gastos / cabeza. Cada uno es un `PrestacionRead` completo. Se puebla solo
    # en `GET /prestaciones/{id}`; en los ítems anidados queda None (sin recursión).
    # `[]` cuando la prestación no pertenece a un equipo. Sirve para que el front, al
    # editar, cargue toda la información del grupo que participó.
    grupo: Optional[list["PrestacionRead"]] = None

    # cod_med/nro_orden/cod_obr (y otros) son columnas enteras en la DB legacy pero
    # se exponen como string en la API → coercionar int→str al leer del ORM.
    @field_validator(
        "cod_medico", "cod_medico_ejecutor", "nro_orden", "cod_obra_social",
        "cod_nomenclador", "dni_paciente", mode="before",
    )
    @classmethod
    def _num_to_str(cls, v):
        return str(v) if v is not None else v

    class Config:
        from_attributes = True
        populate_by_name = True


PrestacionRead.model_rebuild()  # resuelve la auto-referencia de `grupo`


class GuardadoResponse(BaseModel):
    """Respuesta del POST — aplica a 1 o N filas."""
    ids: list[int]
    importe_total: Decimal
    periodo: Optional[str] = None   # período (YYYYMM) donde efectivamente se guardó


class MoverPeriodoResponse(BaseModel):
    ids_movidos: list[int]
    periodo_destino: str


# ── Cierre de período ─────────────────────────────────────────────────────────
# NOTA: POST /cierre es multipart/form-data (cod_obra/periodo van como Form fields,
# el comprobante como File) — no tiene un schema de request Pydantic, ver routes.py.
class CierrePreviewResponse(BaseModel):
    cod_obra: str
    periodo: str
    cantidad: int
    importe_total: Decimal
    cerrado: bool   # True si la última versión ya está cerrada para esa OS+período


class CierreResponse(BaseModel):
    id_factura: int
    cod_obra: str
    periodo: str
    cantidad: int
    importe_total: Decimal
    documento_url: Optional[str] = None
    nro_factura: Optional[str] = None


# ── Factura complementaria (nueva versión de un período+OS ya cerrado) ───────
class ComplementoCreate(BaseModel):
    cod_obra: str
    periodo: str   # "YYYYMM"


# ── Facturas / períodos (cabeceras de `facturacion`) ─────────────────────────
class FacturaRead(BaseModel):
    """Cabecera de `facturacion` — todos los campos de la fila."""
    id_prestaciones: int
    id_cliente: Optional[int] = None
    tipo_factura: Optional[str] = None
    nro_factura: Optional[str] = None
    tipo_factura_2: Optional[str] = None
    nro_factura_2: Optional[str] = None
    tipo_factura_3: Optional[str] = None
    nro_factura_3: Optional[str] = None
    periodo: Optional[str] = None
    periodo_label: Optional[str] = None
    cod_obr: Optional[str] = None
    fecha: Optional[datetime.date] = None
    fecha_envio: Optional[datetime.date] = None
    fecha_recep: Optional[datetime.date] = None
    importe: Optional[Decimal] = None
    afip: Optional[str] = None
    usuario: Optional[str] = None           # NRO_SOCIO de quien creó/cerró la cabecera
    usuario_nombre: Optional[str] = None    # NOMBRE resuelto contra ListadoMedico (batch, no persistido)
    estado: Optional[str] = None            # fase colegio: 'A' abierta / 'C' cerrada
    estado_doctor: Optional[str] = None     # fase médico:  'A' abierta / 'C' cerrada
    created: Optional[datetime.datetime] = None
    documento_url: Optional[str] = None     # comprobante subido al cerrar (si lo hay)
    version: int = 1                        # 1 = original; 2+ = facturas complementarias

    @field_validator("cod_obr", mode="before")
    @classmethod
    def _num_to_str(cls, v):
        return str(v) if v is not None else v

    @field_validator("fecha", "fecha_envio", "fecha_recep", "created", mode="before")
    @classmethod
    def _zero_date_to_none(cls, v):
        # MySQL puede devolver zero-dates ('0000-00-00') que no son fechas válidas.
        if isinstance(v, str) and v.startswith("0000-00-00"):
            return None
        return v

    class Config:
        from_attributes = True


# ── Períodos médico / colegio ────────────────────────────────────────────────
class CierreDoctorPayload(BaseModel):
    cod_obra: str
    periodo: str   # "YYYYMM"


class CierreDoctorResponse(BaseModel):
    cod_obra: str
    periodo: str
    estado_doctor: str
    # Si el puntero de médicos apuntaba al período cerrado, se avanza y acá viene el
    # nuevo período donde los médicos cargan; None si el puntero no estaba ahí.
    periodo_medico_nuevo: Optional[str] = None


class AvanzarPeriodoMedicoPayload(BaseModel):
    # None → avanza el período global; con valor → override de esa OS.
    cod_obra: Optional[str] = None


class AvanzarPeriodoMedicoResponse(BaseModel):
    alcance: str           # "global" o el cod_obra
    periodo_saliente: str
    periodo_nuevo: str
    cabeceras_cerradas: int


class PeriodoMedicoAvanceItem(BaseModel):
    obra_social_id: Optional[int] = None  # None = puntero global
    periodo_saliente: str
    periodo_nuevo: str
    cabeceras_cerradas: int


class CerrarPeriodosVencidosResponse(BaseModel):
    """Response de POST /periodo-medico/cerrar-vencidos (disparado por el cron)."""
    os_avanzadas: int
    cabeceras_cerradas: int
    detalle: list[PeriodoMedicoAvanceItem] = Field(default_factory=list)


class SetPeriodoMedicoPayload(BaseModel):
    # None → fija el puntero global; con valor → el puntero de esa OS.
    cod_obra: Optional[str] = None
    periodo: str = Field(..., pattern=r"^\d{6}$", description="YYYYMM")


class SetPeriodoMedicoResponse(BaseModel):
    alcance: str           # "global" o el cod_obra
    periodo: str


class PeriodoMedicoPunteroOut(BaseModel):
    obra_social_id: Optional[int] = None  # None = global
    obra_social: Optional[str] = None
    es_global: bool
    periodo: str
    periodo_label: str
    updated_at: datetime.datetime


# ── Detalle de factura agrupado por prestador ────────────────────────────────
class PrestacionFacturaDetalleOut(BaseModel):
    id: int
    periodo: str
    autorizacion: Optional[str] = None
    fecha_practica: Optional[datetime.date] = None   # fecha de la prestación (no de carga)
    codigo: Optional[str] = None                     # cod_nom
    nro_afiliado: Optional[str] = None                # dni_p
    # Médico ejecutor — LEGACY, sólo en filas viejas (hoy el ejecutor es el cod_medico
    # del grupo, que es siempre el médico que cobra).
    cod_medico_ejecutor: Optional[str] = None
    nombre_ejecutor: Optional[str] = None
    # Clínica donde se hizo la prestación (prestador o ámbito); None si no hubo.
    cod_clinica: Optional[int] = None
    nombre_clinica: Optional[str] = None
    tipo_orden: Optional[str] = None                 # marca legacy 'S'; discrimina `tipo`
    cantidad: Optional[int] = None
    sesion: Optional[int] = None
    porcentaje: Optional[int] = None                 # porc
    honorarios: Optional[Decimal] = None
    gastos: Optional[Decimal] = None
    tipo_prestador: Optional[str] = None             # badge: Medico | Ayudante | Gastos
    subtotal: Optional[Decimal] = None               # importe_total
    tipo: Optional[str] = None                       # badge: Consulta | Practica | Honorarios individuales | Sanatorio
    revisado: bool = False
    estado: Optional[str] = None

    # codigo/nro_afiliado son columnas enteras en filas legacy → coercionar a string.
    @field_validator("codigo", "nro_afiliado", mode="before")
    @classmethod
    def _num_to_str(cls, v):
        return str(v) if v is not None else v


class PrestadorFacturaGrupoOut(BaseModel):
    cod_medico: str                                  # el médico que cobra
    nombre: Optional[str] = None
    matricula: Optional[int] = None
    # LEGACY: True sólo en grupos históricos donde el payee era una organización. Con el
    # modelo actual el payee es siempre un médico → la clínica se informa por prestación.
    pago_a_clinica: bool = False
    cantidad_prestaciones: int
    total_cantidad: int
    total_honorarios: Decimal
    total_gastos: Decimal
    total_subtotal: Decimal
    prestaciones: list[PrestacionFacturaDetalleOut]

    @field_validator("cod_medico", mode="before")
    @classmethod
    def _num_to_str(cls, v):
        return str(v) if v is not None else v


class FacturaDetalleOut(BaseModel):
    id_factura: int
    periodo: str
    periodo_label: str
    cod_obra: str
    estado: Optional[str] = None
    estado_doctor: Optional[str] = None
    version: int = 1                # 1 = original; 2+ = factura complementaria
    es_complemento: bool = False
    total_prestaciones: int
    total_importe: Decimal
    prestadores: list[PrestadorFacturaGrupoOut]

    @field_validator("cod_obra", mode="before")
    @classmethod
    def _num_to_str(cls, v):
        return str(v) if v is not None else v
