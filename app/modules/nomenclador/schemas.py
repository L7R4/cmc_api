from __future__ import annotations

import datetime
import enum
import re
import unicodedata
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, field_validator, model_validator


# ─────────────────────────────────────────────────────────────────────────────
# Origen / categoría del valor
# ─────────────────────────────────────────────────────────────────────────────

class Origen(str, enum.Enum):
    """Procedencia de la regla de precio. Fija la prioridad del lookup.

    La PRIORIDAD no vive acá: se deriva de la posición en
    ``service.ORIGEN_PRIORIDAD`` (índice 0 = máxima). Sumar un origen = agregar el
    miembro acá + insertarlo en esa tupla; no requiere migración de base.
    """
    NE = "NE"    # Nomenclador Específico (OS + ente de la especialidad)
    NNE = "NNE"  # Nomenclador Negociado (OS)
    NN = "NN"    # Nomenclador Nacional (siempre calculado: unidades × VU pactado)


def slugify_codigo(nombre: str) -> str:
    """Normaliza un nombre a la clave-identidad de galeno en formato
    ``xxxx_xxxx_xxxx``: minúsculas, sin acentos, palabras unidas por guion bajo,
    sin guiones bajos al inicio ni al final.

    Ej.: ``"Galeno Quirúrgico"`` → ``"galeno_quirurgico"``.
    """
    s = unicodedata.normalize("NFKD", nombre or "")
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


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
# Galeno
# ─────────────────────────────────────────────────────────────────────────────

class GalenoCreate(BaseModel):
    obra_social_nro: int
    nombre: str
    nivel: Optional[int] = None
    vigencia_desde: datetime.date
    valor_unitario: Decimal
    # Unidades-plantilla por concepto (defaults al asociar a un código). Nullable.
    unidades_honorarios: Optional[Decimal] = None
    unidades_ayudante: Optional[Decimal] = None
    unidades_gastos: Optional[Decimal] = None
    observacion: Optional[str] = None
    # Clave-identidad derivada de `nombre` (no se envía; se ignora si llega).
    codigo: str = ""

    @model_validator(mode="after")
    def _derivar_codigo(self) -> "GalenoCreate":
        self.codigo = slugify_codigo(self.nombre)
        if not self.codigo:
            raise ValueError("nombre inválido: no genera una clave (codigo)")
        return self


class GalenoUpdate(BaseModel):
    # `nombre` es inmutable tras crear (deriva la clave-identidad usada por
    # vigencias y lookups). Solo se editan metadatos sin efecto sobre la clave.
    # Las unidades NO se editan acá: cambiarlas debe propagar a los valores vigentes,
    # así que tienen su propio endpoint POST /{id}/actualizar_unidades.
    observacion: Optional[str] = None


class GalenoActualizarPrecioIn(BaseModel):
    nuevo_valor_unitario: Decimal
    vigencia_desde: datetime.date


class GalenoNivelItem(BaseModel):
    nivel: Optional[int] = None
    valor_unitario: Decimal
    # Unidades-plantilla por nivel (cada nivel puede pactar sus propias unidades).
    unidades_honorarios: Optional[Decimal] = None
    unidades_ayudante: Optional[Decimal] = None
    unidades_gastos: Optional[Decimal] = None


class GalenoCrearNivelesIn(BaseModel):
    """Alta de un galeno nivelado: una fila por nivel con su valor unitario."""
    obra_social_nro: int
    nombre: str
    vigencia_desde: datetime.date
    niveles: List[GalenoNivelItem]
    observacion: Optional[str] = None
    # Clave-identidad derivada de `nombre` (no se envía).
    codigo: str = ""

    @model_validator(mode="after")
    def check_niveles(self) -> "GalenoCrearNivelesIn":
        self.codigo = slugify_codigo(self.nombre)
        if not self.codigo:
            raise ValueError("nombre inválido: no genera una clave (codigo)")
        if not self.niveles:
            raise ValueError("Debe incluir al menos un nivel")
        vistos = set()
        for item in self.niveles:
            if item.nivel in vistos:
                raise ValueError(f"Nivel {item.nivel} repetido")
            vistos.add(item.nivel)
        return self


class GalenoPrecioMasivoItem(BaseModel):
    nivel: Optional[int] = None
    nuevo_valor_unitario: Decimal


class GalenoActualizarPrecioMasivoIn(BaseModel):
    """
    Actualiza el precio de todas las filas vigentes de un galeno (todos sus niveles)
    en una sola operación. O bien `porcentaje` (aplica a todos los niveles vigentes),
    o bien `items` con el valor explícito por nivel.
    """
    obra_social_nro: int
    codigo: str
    vigencia_desde: datetime.date
    porcentaje: Optional[Decimal] = None
    items: Optional[List[GalenoPrecioMasivoItem]] = None

    @model_validator(mode="after")
    def check_modo(self) -> "GalenoActualizarPrecioMasivoIn":
        if (self.porcentaje is None) == (self.items is None):
            raise ValueError("Debe proveer 'porcentaje' o 'items' (exactamente uno)")
        return self


class GalenoOut(BaseModel):
    id: int
    obra_social_nro: int
    codigo: str
    nombre: str
    nivel: Optional[int]
    vigencia_desde: datetime.date
    vigencia_hasta: Optional[datetime.date]
    valor_unitario: Decimal
    unidades_honorarios: Optional[Decimal]
    unidades_ayudante: Optional[Decimal]
    unidades_gastos: Optional[Decimal]
    activo: bool
    observacion: Optional[str]
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


class GalenoActualizarUnidadesIn(BaseModel):
    """
    Cambia las unidades-plantilla de un galeno y PROPAGA el cambio a los valores
    vigentes: pisa la cantidad de todos los componentes activos que usan el galeno en
    los conceptos provistos (opción 'pisar todos') y regenera el historial.
    Solo se propagan los conceptos cuyo campo se envía con un valor no nulo.
    """
    vigencia_desde: datetime.date
    unidades_honorarios: Optional[Decimal] = None
    unidades_ayudante: Optional[Decimal] = None
    unidades_gastos: Optional[Decimal] = None


class GalenoActualizarUnidadesResult(BaseModel):
    galeno: GalenoOut
    componentes_actualizados: int


class GalenosImportarIn(BaseModel):
    """Importa los galenos vigentes de una OS origen a una OS destino."""
    obra_social_nro_origen: int
    obra_social_nro_destino: int
    vigencia_desde: datetime.date
    # Limita la importación a estos códigos (None o lista vacía = todos).
    codigos: Optional[List[str]] = None
    # Si el origen tiene un galeno nivelado y el destino lo tiene sin nivel,
    # reemplaza el sin-nivel del destino por los niveles del origen (reapunta
    # los valores del destino al galeno del nivel de cada valor).
    convertir_a_nivelado: bool = False
    # Copia solo el valor_unitario del origen y conserva las unidades del destino
    # (para galenos que ya existen en el destino).
    solo_valor: bool = False

    @model_validator(mode="after")
    def _distintas(self) -> "GalenosImportarIn":
        if self.obra_social_nro_origen == self.obra_social_nro_destino:
            raise ValueError("La OS origen y destino deben ser distintas")
        return self


class GalenosImportarResult(BaseModel):
    total_origen: int
    creados: int          # galenos nuevos en el destino
    rotados: int          # galenos del destino rotados (cerró vigente + abrió nuevo)
    sin_cambios: int      # ya estaban idénticos en el destino
    convertidos: int = 0  # códigos del destino convertidos de sin-nivel a nivelado
    errores: List[dict]


# ─────────────────────────────────────────────────────────────────────────────
# Valor + ValorComponente
# ─────────────────────────────────────────────────────────────────────────────

class ValorComponenteIn(BaseModel):
    concepto: Literal["Honorarios", "Ayudante", "Gastos"]
    galeno_id: Optional[int] = None
    cantidad: Decimal = Decimal("0")
    valor_unitario: Optional[Decimal] = None
    orden: int = 0
    observacion: Optional[str] = None

    @model_validator(mode="after")
    def check_galeno_or_fijo(self) -> "ValorComponenteIn":
        if self.cantidad > 0 and self.galeno_id is None:
            raise ValueError("cantidad > 0 requiere galeno_id")
        if self.cantidad == 0 and self.galeno_id is None and self.valor_unitario is None:
            raise ValueError("cantidad = 0 sin galeno_id requiere valor_unitario (precio fijo)")
        return self


def validar_lista_componentes(componentes: List[ValorComponenteIn]) -> None:
    """
    Reglas de la ecuación de un Valor:
    - Modalidad homogénea: todos calculables (con galeno) o todos fijos. Sin mezcla.
    - Máximo un componente por concepto (Honorarios / Ayudante / Gastos).
    """
    if not componentes:
        raise ValueError("El valor requiere al menos un componente")
    con_galeno = [c for c in componentes if c.galeno_id is not None]
    if con_galeno and len(con_galeno) != len(componentes):
        raise ValueError(
            "Modalidad mixta no permitida: todos los componentes deben ser "
            "calculables (galeno) o todos fijos"
        )
    conceptos = [c.concepto for c in componentes]
    repetidos = {c for c in conceptos if conceptos.count(c) > 1}
    if repetidos:
        raise ValueError(f"Concepto repetido: {', '.join(sorted(repetidos))} (máximo uno por concepto)")


def validar_reglas_origen(
    origen: str,
    especialidad_id_colegio: Optional[int],
    por_presupuesto: bool,
    es_galeno: Optional[bool],
) -> None:
    """
    Reglas transversales por origen (válidas tanto en Pydantic como server-side):
    - especialidad_id_colegio solo lo admite NE (NNE/NN van sin perfil).
    - NN es siempre calculado: exige modalidad galeno y no puede ser por_presupuesto.

    `es_galeno`: True si la modalidad es calculable (galeno), False si fija, None si
    no aplica/desconocida (p.ej. por_presupuesto, donde no hay ecuación que evaluar).
    """
    if especialidad_id_colegio is not None and origen != Origen.NE.value:
        raise ValueError(
            "especialidad_id_colegio solo es válido para origen NE; "
            "NNE y NN deben ir sin especialidad"
        )
    if origen == Origen.NN.value:
        if por_presupuesto:
            raise ValueError("El origen NN es siempre calculado: no admite 'por_presupuesto'")
        if es_galeno is False:
            raise ValueError(
                "El origen NN exige modalidad galeno (todos los componentes calculables)"
            )


class ValorComponenteOut(BaseModel):
    id: int
    valor_id: int
    concepto: str
    # Discriminador: 'calculable' (galeno × cantidad) | 'fijo' (precio embebido)
    tipo: str
    galeno_id: Optional[int]
    galeno_codigo: Optional[str] = None
    galeno_nivel: Optional[int] = None
    cantidad: Decimal
    # valor_unitario = valor CRUDO almacenado (null en calculables, es lo esperado).
    # precio_unitario = VU EFECTIVO a mostrar (del galeno si es calculable, del fijo si no).
    valor_unitario: Optional[Decimal]
    precio_unitario: Optional[Decimal] = None
    subtotal: Decimal
    orden: int
    activo: bool
    observacion: Optional[str]

    model_config = {"from_attributes": True}


class ValorCreate(BaseModel):
    obra_social_nro: int
    nomenclador_id: int
    # Categoría/procedencia: fija la prioridad del lookup (NE > NNE > NN)
    origen: Origen
    descripcion: Optional[str] = None
    nivel: Optional[int] = None
    complejidad: Optional[Literal["baja", "media", "alta"]] = None
    # Perfil que cobra esta variante: NULL = sin especialidad; N = exige esa especialidad.
    # Solo lo admite NE (NNE/NN van NULL).
    especialidad_id_colegio: Optional[int] = None
    # True → código por presupuesto: se ignora la ecuación, los componentes H/G/A
    # se guardan en 0 y el monto lo informa la OS al facturar (modo manual)
    por_presupuesto: bool = False
    vigencia_desde: datetime.date
    observacion: Optional[str] = None
    componentes: List[ValorComponenteIn] = []

    @model_validator(mode="after")
    def check_componentes(self) -> "ValorCreate":
        es_galeno = None
        if not self.por_presupuesto:
            validar_lista_componentes(self.componentes)
            es_galeno = any(c.galeno_id is not None for c in self.componentes)
        validar_reglas_origen(
            self.origen.value, self.especialidad_id_colegio, self.por_presupuesto, es_galeno
        )
        return self


class ValorUpdate(BaseModel):
    # especialidad_id_colegio NO es editable: es la identidad de la variante
    descripcion: Optional[str] = None
    nivel: Optional[int] = None
    complejidad: Optional[Literal["baja", "media", "alta"]] = None
    observacion: Optional[str] = None


class ValorCerrarYCrearIn(BaseModel):
    # La variante (especialidad_id_colegio) se conserva del valor que se cierra
    vigencia_desde: datetime.date
    componentes: List[ValorComponenteIn] = []
    por_presupuesto: bool = False
    descripcion: Optional[str] = None
    nivel: Optional[int] = None
    complejidad: Optional[Literal["baja", "media", "alta"]] = None
    observacion: Optional[str] = None

    @model_validator(mode="after")
    def check_componentes(self) -> "ValorCerrarYCrearIn":
        if not self.por_presupuesto:
            validar_lista_componentes(self.componentes)
        return self


class ValorOut(BaseModel):
    id: int
    obra_social_nro: int
    nomenclador_id: int
    origen: str
    codigo: str
    descripcion: Optional[str]
    nivel: Optional[int]
    complejidad: Optional[str]
    especialidad_id_colegio: Optional[int]
    por_presupuesto: bool = False
    # Modalidad de la ecuación: 'galeno' | 'fijo' | 'por_presupuesto'
    modalidad: str
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
    origen: Origen                               # scope: solo valores de este origen
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
    origen: Origen                               # scope: variante (origen, sin especialidad)
    vigencia_desde: datetime.date
    items: List[ActualizarPorCodigosItem]


class RevertirActualizacionIn(BaseModel):
    obra_social_nro: int
    vigencia_revertir: datetime.date


class ActualizacionMasivaResult(BaseModel):
    actualizados: int
    errores: List[dict]
    # Valores fuera del alcance de la operación (ej: calculables en una
    # actualización de precios fijos) — no son errores
    omitidos: int = 0


class GenerarValoresNNIn(BaseModel):
    """Seed de valores NN por rangos de código para una obra social."""
    obra_social_nro: int
    vigencia_desde: datetime.date


class GenerarValoresNNResult(BaseModel):
    total_candidatos: int   # códigos en rango (1..419999) con >=1 unidad
    creados: int            # valores NN nuevos
    recreados: int          # tenían NN activo → se cerró y recreó
    errores: List[dict]     # [{codigo, motivo}] (ej: galeno faltante en la OS)


# ─────────────────────────────────────────────────────────────────────────────
# Replicación de estructura (ecuación de componentes)
# ─────────────────────────────────────────────────────────────────────────────

class ReplicarDestinoItem(BaseModel):
    nomenclador_id: int
    nivel: Optional[int] = None   # None → conserva el nivel del valor origen


class ReplicarEstructuraIn(BaseModel):
    """
    Toma la ecuación de componentes de un Valor origen y la replica en N códigos
    destino de la misma OS. Para cada destino:
    - si tiene `nivel`, los componentes con galeno nivelado se remapean al galeno
      vigente del mismo codigo de galeno y ese nivel;
    - si `usar_unidades_nomenclador=True`, la cantidad de cada componente calculable
      se re-resuelve con las unidades por defecto del código destino.
    """
    origen_valor_id: int
    destinos: List[ReplicarDestinoItem]
    vigencia_desde: datetime.date
    usar_unidades_nomenclador: bool = True


class ReplicarObrasSocialesIn(BaseModel):
    """
    Replica la configuración de un Valor (mismo código CMC, mismo origen, misma
    especialidad, mismo nivel y misma ecuación/unidades) hacia varias obras sociales.

    El precio NO se copia tal cual entre OS:
    - Modalidad galeno: se referencia el galeno propio de cada OS (codigo+nivel); el
      VU pactado de esa OS es "lo único que cambia". Si la OS destino no tiene el galeno
      vigente, ese destino queda en `errores`.
    - Modalidad fija: con `incluir_precio_fijo=True` se copia el `valor_unitario`; con
      False el destino queda en `errores` (un valor fijo sin precio no es válido).

    `modo`:
      - "subset" → `obras_sociales` son los destinos.
      - "todas"  → todas las OS habilitadas (MARCA="S") MENOS `obras_sociales` (exclusiones).
    """
    origen_valor_id: int
    modo: Literal["subset", "todas"] = "subset"
    obras_sociales: List[int] = []   # subset: destinos · todas: exclusiones
    vigencia_desde: datetime.date
    usar_unidades_nomenclador: bool = False
    incluir_precio_fijo: bool = True

    @model_validator(mode="after")
    def check_modo(self) -> "ReplicarObrasSocialesIn":
        if self.modo == "subset" and not self.obras_sociales:
            raise ValueError("modo 'subset' requiere al menos una obra social destino")
        return self


# ─────────────────────────────────────────────────────────────────────────────
# Lookup de precio
# ─────────────────────────────────────────────────────────────────────────────


class LookupPrecioIn(BaseModel):
    codigo_origen: Optional[str] = None       # código de la OS (pasa por homologador)
    codigo_colegio: Optional[str] = None      # código CMC directo
    obra_social_nro: int
    fecha_practica: datetime.date
    medico_id: int

    @model_validator(mode="after")
    def check_codigo(self) -> "LookupPrecioIn":
        if not self.codigo_origen and not self.codigo_colegio:
            raise ValueError("Debe proveer codigo_origen o codigo_colegio")
        return self


class ComponenteLookupOut(BaseModel):
    componente_id: Optional[int] = None
    concepto: str
    tipo: Literal["fijo", "calculable"]
    galeno_id: Optional[int]
    galeno_codigo: Optional[str]
    galeno_nivel: Optional[int] = None
    cantidad: Decimal
    valor_unitario: Decimal
    subtotal: Decimal


class LookupPrecioOut(BaseModel):
    nomenclador_id: int
    codigo_colegio: str
    descripcion: Optional[str]
    obra_social_nro: int
    nivel: Optional[int]
    # Origen de la variante elegida (NE|NNE|NN) — la de mayor prioridad aplicable
    origen: str
    # Variante elegida: NULL = sin especialidad, N = variante por especialidad N
    variante_especialidad_id: Optional[int] = None
    # True → código por presupuesto: precio 0, el monto lo carga el operador a mano
    por_presupuesto: bool = False
    fecha_practica: datetime.date
    precio_base: Decimal            # = precio_total (ya no hay opcionales)
    precio_total: Decimal           # suma de los 3 componentes
    componentes: List[ComponenteLookupOut]


# ─────────────────────────────────────────────────────────────────────────────
# HistorialPrecioCodigo
# ─────────────────────────────────────────────────────────────────────────────

class HistorialPrecioOut(BaseModel):
    id: int
    nomenclador_id: int
    obra_social_nro: int
    origen: str
    especialidad_id_colegio: Optional[int] = None
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


class BoletinItemOut(BaseModel):
    codigo: str
    origen: str
    descripcion: Optional[str]
    nivel: Optional[int]
    por_presupuesto: bool = False
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
    origen: str
    # Especialidad de la variante elegida (ID_COLEGIO_ESPE). NULL = variante sin
    # perfil (NNE/NN o NE sin especialidad). Se puebla al filtrar por especialidades.
    especialidad_id_colegio: Optional[int] = None
    descripcion: Optional[str]
    nivel: Optional[int]
    por_presupuesto: bool = False
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
