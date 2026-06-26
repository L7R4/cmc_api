import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# ── Tipos literales ──────────────────────────────────────────────────────────
TipoCalculo = Literal["A", "M"]


# ── Afiliados ────────────────────────────────────────────────────────────────
class AfiliadoCreate(BaseModel):
    dni: str = Field(..., min_length=6, max_length=15, pattern=r"^\d+$")
    nombre: str = Field(..., min_length=1, max_length=100)


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

    `periodo` y `nro_orden` no se envían: los asigna el backend. El nombre del
    paciente tampoco: se obtiene del padrón `afiliado` por `dni_paciente`.
    """
    # Médico
    cod_medico: str   # NRO_SOCIO del médico

    # Paciente — solo DNI; el nombre se resuelve contra el padrón afiliado
    dni_paciente: Optional[str] = None

    # Servicio
    fecha_practica: Optional[datetime.date] = None   # None → primer día del período
    cod_clinica: Optional[int] = None                # si viene → tipo = "Sanatorio"

    # Prestación
    cod_nomenclador: str
    cantidad: int = Field(1, ge=1)
    sesion: int = Field(1, ge=1)

    # Montos — el concepto en >0 es el que se factura (médico/gastos/ayudante implícito).
    # Automático: el backend toma el valor del lookup para cada concepto marcado en >0.
    # Manual: se guardan los montos enviados tal cual.
    tipo_calculo: TipoCalculo = "A"
    honorarios: Optional[Decimal] = None
    gastos: Optional[Decimal] = None
    ayudante: Optional[Decimal] = None
    porcentaje: int = Field(100, ge=1, le=100)

    # Vínculo a la fila del médico (cabeza del equipo) cuando el ayudante se carga aparte.
    grupo_equipo_id: Optional[int] = None


class PrestacionesCreate(BaseModel):
    """Payload del POST /facturacion/prestaciones.

    1 ítem = carga individual. N ítems = equipo quirúrgico (misma transacción,
    `nro_orden` único por fila). `periodo` lo calcula el backend según la OS.
    """
    obra_social: str                      # cod_obra_social
    prestaciones: list[PrestacionItem] = Field(..., min_length=1)


class PrestacionUpdate(BaseModel):
    """PATCH — todos los campos opcionales. No se puede cambiar periodo, cod_obra
    ni nro_orden. Si se envía dni_paciente, se relee el nombre del padrón."""
    cod_medico: Optional[str] = None
    dni_paciente: Optional[str] = None
    fecha_practica: Optional[datetime.date] = None
    cod_clinica: Optional[int] = None
    cod_nomenclador: Optional[str] = None
    cantidad: Optional[int] = Field(None, ge=1)
    sesion: Optional[int] = Field(None, ge=1)
    tipo_calculo: Optional[TipoCalculo] = None
    honorarios: Optional[Decimal] = None
    gastos: Optional[Decimal] = None
    ayudante: Optional[Decimal] = None
    porcentaje: Optional[int] = Field(None, ge=1, le=100)
    grupo_equipo_id: Optional[int] = None


class MoverPeriodoPayload(BaseModel):
    """Mueve un conjunto de prestaciones al período siguiente o anterior.
    Todas deben pertenecer a la misma OS y período origen."""
    cod_obra: str
    periodo_origen: str               # "YYYYMM"
    nro_ordenes: list[str] = Field(..., min_length=1)
    direccion: Literal["siguiente", "anterior"]


# ── Respuestas ───────────────────────────────────────────────────────────────
class PeriodoActivoResponse(BaseModel):
    cod_obra: str
    periodo: str        # "YYYYMM"
    periodo_label: str  # "Mayo 2026"


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


class PrestacionRead(BaseModel):
    id: int = Field(..., alias="id_detalle_prestaciones")
    periodo: str
    cod_medico: str = Field(..., alias="cod_med")
    nro_orden: Optional[str] = None
    cod_obra_social: Optional[str] = Field(None, alias="cod_obr")
    cod_nomenclador: Optional[str] = Field(None, alias="cod_nom")
    tipo: Optional[str] = None
    grupo_equipo_id: Optional[int] = None
    sesion: Optional[int] = None
    cantidad: Optional[int] = None
    honorarios: Optional[Decimal] = None
    gastos: Optional[Decimal] = None
    ayudante: Optional[Decimal] = None
    importe_total: Optional[Decimal] = None
    estado: Optional[str] = None
    fecha_practica: Optional[datetime.date] = None
    dni_paciente: Optional[str] = Field(None, alias="dni_p")
    nombre_paciente: Optional[str] = Field(None, alias="nom_ape_p")

    # cod_med/nro_orden/cod_obr (y otros) son columnas enteras en la DB legacy pero
    # se exponen como string en la API → coercionar int→str al leer del ORM.
    @field_validator(
        "cod_medico", "nro_orden", "cod_obra_social", "cod_nomenclador",
        "dni_paciente", mode="before",
    )
    @classmethod
    def _num_to_str(cls, v):
        return str(v) if v is not None else v

    class Config:
        from_attributes = True
        populate_by_name = True


class GuardadoResponse(BaseModel):
    """Respuesta del POST — aplica a 1 o N filas."""
    ids: list[int]
    importe_total: Decimal


class MoverPeriodoResponse(BaseModel):
    ids_movidos: list[int]
    periodo_destino: str


# ── Cierre de período ─────────────────────────────────────────────────────────
class CierrePayload(BaseModel):
    cod_obra: str
    periodo: str   # "YYYYMM"


class CierrePreviewResponse(BaseModel):
    cod_obra: str
    periodo: str
    cantidad: int
    importe_total: Decimal
    cerrado: bool   # True si ya existe factura cerrada para esa OS+período


class CierreResponse(BaseModel):
    id_factura: int
    cod_obra: str
    periodo: str
    cantidad: int
    importe_total: Decimal
