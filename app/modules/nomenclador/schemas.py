from __future__ import annotations

import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, field_validator, model_validator


# ─────────────────────────────────────────────────────────────────────────────
# NomencladorCMC
# ─────────────────────────────────────────────────────────────────────────────

class NomencladorCreate(BaseModel):
    codigo: str
    proviene_de_id: Optional[int] = None
    descripcion: str
    categoria: Optional[str] = None
    complejidad: Optional[Literal["baja", "media", "alta"]] = None
    sin_restriccion_especialidad: bool = False
    unidades_honorarios: Optional[Decimal] = None
    unidades_ayudante: Optional[Decimal] = None
    unidades_gastos: Optional[Decimal] = None
    observacion: Optional[str] = None

    @field_validator("proviene_de_id", mode="before")
    @classmethod
    def coerce_zero_to_none(cls, v: object) -> Optional[int]:
        return None if v == 0 else v


class NomencladorUpdate(BaseModel):
    descripcion: Optional[str] = None
    categoria: Optional[str] = None
    complejidad: Optional[Literal["baja", "media", "alta"]] = None
    sin_restriccion_especialidad: Optional[bool] = None
    unidades_honorarios: Optional[Decimal] = None
    unidades_ayudante: Optional[Decimal] = None
    unidades_gastos: Optional[Decimal] = None
    activo: Optional[bool] = None
    observacion: Optional[str] = None


class NomencladorOut(BaseModel):
    id: int
    codigo: str
    proviene_de_id: Optional[int]
    descripcion: str
    categoria: Optional[str]
    complejidad: Optional[str]
    sin_restriccion_especialidad: bool
    unidades_honorarios: Optional[Decimal]
    unidades_ayudante: Optional[Decimal]
    unidades_gastos: Optional[Decimal]
    activo: bool
    observacion: Optional[str]
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = {"from_attributes": True}


class NomencladorConVariantesOut(NomencladorOut):
    variantes: List[NomencladorOut] = []


# ─────────────────────────────────────────────────────────────────────────────
# NomencladorEspecialidad
# ─────────────────────────────────────────────────────────────────────────────

class NomencladorEspecialidadCreate(BaseModel):
    especialidad_id_colegio: int
    observacion: Optional[str] = None


class NomencladorEspecialidadOut(BaseModel):
    id: int
    nomenclador_id: int
    especialidad_id_colegio: int
    activo: bool
    observacion: Optional[str]
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# MedicoCodigoHabilitado
# ─────────────────────────────────────────────────────────────────────────────

class MedicoHabilitacionCreate(BaseModel):
    medico_id: int
    tipo: Literal["habilita", "inhabilita"]
    vigencia_desde: Optional[datetime.date] = None
    vigencia_hasta: Optional[datetime.date] = None
    motivo: Optional[str] = None
    observacion: Optional[str] = None


class MedicoHabilitacionUpdate(BaseModel):
    tipo: Optional[Literal["habilita", "inhabilita"]] = None
    vigencia_desde: Optional[datetime.date] = None
    vigencia_hasta: Optional[datetime.date] = None
    motivo: Optional[str] = None
    observacion: Optional[str] = None
    activo: Optional[bool] = None


class MedicoHabilitacionOut(BaseModel):
    id: int
    medico_id: int
    nomenclador_id: int
    tipo: str
    vigencia_desde: Optional[datetime.date]
    vigencia_hasta: Optional[datetime.date]
    motivo: Optional[str]
    observacion: Optional[str]
    activo: bool
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# Homologador
# ─────────────────────────────────────────────────────────────────────────────

class HomologadorCreate(BaseModel):
    obra_social_nro: int
    codigo_origen: str
    nomenclador_id: int
    descripcion_origen: Optional[str] = None
    observacion: Optional[str] = None


class HomologadorUpdate(BaseModel):
    nomenclador_id: Optional[int] = None
    descripcion_origen: Optional[str] = None
    observacion: Optional[str] = None
    activo: Optional[bool] = None


class HomologadorOut(BaseModel):
    id: int
    obra_social_nro: int
    codigo_origen: str
    nomenclador_id: int
    descripcion_origen: Optional[str]
    observacion: Optional[str]
    activo: bool
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


class HomologadorValidarIn(BaseModel):
    obra_social_nro: int
    codigo_origen: str


class HomologadorValidarOut(BaseModel):
    nomenclador: NomencladorOut
    homologador_id: int


class HomologadorImportarCSVResult(BaseModel):
    procesados: int
    errores: List[dict]


# ─────────────────────────────────────────────────────────────────────────────
# Convenio
# ─────────────────────────────────────────────────────────────────────────────

class ConvenioCreate(BaseModel):
    obra_social_nro: int
    nombre: str
    fecha_inicio: datetime.date
    observacion: Optional[str] = None


class ConvenioUpdate(BaseModel):
    nombre: Optional[str] = None
    observacion: Optional[str] = None


class ConvenioCerrarIn(BaseModel):
    fecha_fin: Optional[datetime.date] = None  # default: fecha_inicio del nuevo - 1 día


class ConvenioOut(BaseModel):
    id: int
    obra_social_nro: int
    nombre: str
    fecha_inicio: datetime.date
    fecha_fin: Optional[datetime.date]
    estado: str
    observacion: Optional[str]
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = {"from_attributes": True}




# ─────────────────────────────────────────────────────────────────────────────
# Galeno
# ─────────────────────────────────────────────────────────────────────────────

class GalenoCreate(BaseModel):
    obra_social_nro: int
    convenio_id: int
    codigo: str
    nombre: str
    tipo: Literal["galeno", "gasto", "modulo", "otro"]
    vigencia_desde: datetime.date
    valor_unitario: Decimal
    observacion: Optional[str] = None


class GalenoUpdate(BaseModel):
    nombre: Optional[str] = None
    tipo: Optional[Literal["galeno", "gasto", "modulo", "otro"]] = None
    observacion: Optional[str] = None


class GalenoActualizarPrecioIn(BaseModel):
    nuevo_valor_unitario: Decimal
    vigencia_desde: datetime.date


class GalenoOut(BaseModel):
    id: int
    obra_social_nro: int
    convenio_id: int
    codigo: str
    nombre: str
    tipo: str
    vigencia_desde: datetime.date
    vigencia_hasta: Optional[datetime.date]
    valor_unitario: Decimal
    activo: bool
    observacion: Optional[str]
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# Valor + ValorComponente
# ─────────────────────────────────────────────────────────────────────────────

class ValorComponenteIn(BaseModel):
    concepto: Literal["Honorarios", "Ayudante", "Gastos"]
    galeno_id: Optional[int] = None
    cantidad: Decimal = Decimal("0")
    valor_unitario: Optional[Decimal] = None
    opcional: bool = False
    orden: int = 0
    observacion: Optional[str] = None

    @model_validator(mode="after")
    def check_galeno_or_fijo(self) -> "ValorComponenteIn":
        if self.cantidad > 0 and self.galeno_id is None:
            raise ValueError("cantidad > 0 requiere galeno_id")
        if self.cantidad == 0 and self.galeno_id is None and self.valor_unitario is None:
            raise ValueError("cantidad = 0 sin galeno_id requiere valor_unitario (precio fijo)")
        return self


class ValorComponenteOut(BaseModel):
    id: int
    valor_id: int
    concepto: str
    galeno_id: Optional[int]
    cantidad: Decimal
    valor_unitario: Optional[Decimal]
    opcional: bool
    orden: int
    activo: bool
    observacion: Optional[str]

    model_config = {"from_attributes": True}


class ValorCreate(BaseModel):
    obra_social_nro: int
    convenio_id: int
    nomenclador_id: int
    descripcion: Optional[str] = None
    nivel: Optional[int] = None
    complejidad: Optional[Literal["baja", "media", "alta"]] = None
    vigencia_desde: datetime.date
    observacion: Optional[str] = None
    componentes: List[ValorComponenteIn]


class ValorUpdate(BaseModel):
    descripcion: Optional[str] = None
    nivel: Optional[int] = None
    complejidad: Optional[Literal["baja", "media", "alta"]] = None
    observacion: Optional[str] = None


class ValorCerrarYCrearIn(BaseModel):
    vigencia_desde: datetime.date
    componentes: List[ValorComponenteIn]
    descripcion: Optional[str] = None
    nivel: Optional[int] = None
    complejidad: Optional[Literal["baja", "media", "alta"]] = None
    observacion: Optional[str] = None


class ValorOut(BaseModel):
    id: int
    obra_social_nro: int
    convenio_id: int
    nomenclador_id: int
    codigo: str
    descripcion: Optional[str]
    nivel: Optional[int]
    complejidad: Optional[str]
    vigencia_desde: datetime.date
    vigencia_hasta: Optional[datetime.date]
    estado: str
    observacion: Optional[str]
    componentes: List[ValorComponenteOut] = []
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# Actualizaciones masivas
# ─────────────────────────────────────────────────────────────────────────────

class ActualizarPorcentajeIn(BaseModel):
    obra_social_nro: int
    convenio_id: int
    porcentaje: Decimal  # ej. 15.5 → +15,5%
    vigencia_desde: datetime.date
    filtro_codigos: Optional[List[str]] = None   # None = todos
    filtro_rango: Optional[dict] = None          # {"desde": "080000", "hasta": "089999"}


class ActualizarPorCodigosItem(BaseModel):
    nomenclador_id: int
    nuevo_valor_unitario: Decimal
    nuevo_nivel: Optional[int] = None


class ActualizarPorCodigosIn(BaseModel):
    obra_social_nro: int
    convenio_id: int
    vigencia_desde: datetime.date
    items: List[ActualizarPorCodigosItem]


class RevertirActualizacionIn(BaseModel):
    obra_social_nro: int
    convenio_id: int
    vigencia_revertir: datetime.date


class ActualizacionMasivaResult(BaseModel):
    actualizados: int
    errores: List[dict]


# ─────────────────────────────────────────────────────────────────────────────
# Lookup de precio
# ─────────────────────────────────────────────────────────────────────────────

class LookupPrecioIn(BaseModel):
    codigo_origen: Optional[str] = None       # código de la OS (pasa por homologador)
    codigo_colegio: Optional[str] = None      # código CMC directo
    obra_social_nro: int
    fecha_practica: datetime.date
    medico_id: int
    opcionales_activos: List[int] = []        # IDs de ValorComponente opcionales a incluir

    @model_validator(mode="after")
    def check_codigo(self) -> "LookupPrecioIn":
        if not self.codigo_origen and not self.codigo_colegio:
            raise ValueError("Debe proveer codigo_origen o codigo_colegio")
        return self


class ComponenteLookupOut(BaseModel):
    concepto: str
    tipo: Literal["fijo", "calculable"]
    galeno_id: Optional[int]
    galeno_codigo: Optional[str]
    cantidad: Decimal
    valor_unitario: Decimal
    subtotal: Decimal
    opcional: bool
    incluido: bool


class LookupPrecioOut(BaseModel):
    nomenclador_id: int
    codigo_colegio: str
    descripcion: Optional[str]
    obra_social_nro: int
    convenio_id: int
    fecha_practica: datetime.date
    precio_base: Decimal            # solo componentes obligatorios
    precio_total: Decimal           # base + opcionales activados
    componentes: List[ComponenteLookupOut]


# ─────────────────────────────────────────────────────────────────────────────
# HistorialPrecioCodigo
# ─────────────────────────────────────────────────────────────────────────────

class HistorialPrecioOut(BaseModel):
    id: int
    nomenclador_id: int
    obra_social_nro: int
    convenio_id: int
    vigencia_desde: datetime.date
    vigencia_hasta: Optional[datetime.date]
    precio_total: Decimal
    valores_id: int
    componentes_snapshot: list
    motivo_cambio: str
    referencia_cambio_id: Optional[int]
    fecha_cambio: datetime.datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# Importar CSV
# ─────────────────────────────────────────────────────────────────────────────

class ImportarCSVResult(BaseModel):
    procesados: int
    errores: List[dict]


# ─────────────────────────────────────────────────────────────────────────────
# Reportes
# ─────────────────────────────────────────────────────────────────────────────

class RankingItem(BaseModel):
    posicion: int
    obra_social_nro: int
    nombre_os: str
    valor: Decimal


class RankingValoresOut(BaseModel):
    fecha_referencia: datetime.date
    codigo_consulta: str
    ranking: List[RankingItem]


class BoletinComponenteOut(BaseModel):
    concepto: str
    tipo: str
    valor_unitario: Optional[Decimal]
    cantidad: Optional[Decimal]
    subtotal: Decimal
    opcional: bool


class BoletinItemOut(BaseModel):
    codigo: str
    descripcion: Optional[str]
    nivel: Optional[int]
    precio_total: Decimal
    componentes: List[BoletinComponenteOut]
    vigencia_desde: datetime.date
    vigencia_hasta: Optional[datetime.date]


class BoletinOut(BaseModel):
    fecha: datetime.date
    obra_social_nro: Optional[int]
    items: List[BoletinItemOut]


class TablaValoresItem(BaseModel):
    nomenclador_id: int
    codigo: str
    descripcion: Optional[str]
    nivel: Optional[int]
    precio_total: Decimal
    vigencia_desde: datetime.date
    vigencia_hasta: Optional[datetime.date]
    componentes: list


class EvolucionPrecioItem(BaseModel):
    vigencia_desde: datetime.date
    vigencia_hasta: Optional[datetime.date]
    precio_total: Decimal
    motivo_cambio: str
    fecha_cambio: datetime.datetime
