from __future__ import annotations

import datetime
import enum
import re
import unicodedata
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ─────────────────────────────────────────────────────────────────────────────
# Origen / categoría del valor
# ─────────────────────────────────────────────────────────────────────────────

class Origen(str, enum.Enum):
    """Procedencia de la regla de precio. Fija la prioridad del lookup.

    La PRIORIDAD no vive acá: se deriva de la posición en
    ``service.ORIGEN_PRIORIDAD`` (índice 0 = máxima). Sumar un origen = agregar el
    miembro acá + insertarlo en esa tupla; no requiere migración de base.

    NNE (Nomenclador Negociado) se eliminó: era un atajo de almacenamiento para "vale
    para cualquier especialidad habilitada". Toda variante de convenio es ahora NE con
    una fila explícita por especialidad (ver ``validar_reglas_origen``).
    """
    NE = "NE"  # Nomenclador Específico (OS + especialidad habilitada para el código)
    NN = "NN"  # Nomenclador Nacional (siempre calculado: unidades × VU pactado)


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
    """Alta de un código del Colegio: código + clasificación + datos default del
    catálogo (descripción, "sin restricción") + plantilla de especialidades sugeridas.
    Todo lo "default" es sólo una sugerencia: los datos que mandan son POR OBRA SOCIAL
    (`Valor.descripcion`, `Valor.sin_restriccion_especialidad`, `nm_valor_especialidad`)
    y se escriben al aplicar la plantilla a una obra social
    (`POST /{id}/aplicar-especialidades`) o desde el modal de Valores."""
    codigo: str
    descripcion: Optional[str] = None
    categoria: Optional[str] = None
    complejidad: Optional[Literal["baja", "media", "alta"]] = None
    # Código NN al que corresponde este código del Colegio (opcional). Alimenta la
    # generación automática de Valores NN — ver NomencladorNacional.
    nomenclador_nacional_id: Optional[int] = None
    observacion: Optional[str] = None
    sin_restriccion_especialidad: Optional[bool] = None
    # ID_COLEGIO_ESPE de las especialidades sugeridas (plantilla). Ignorada si
    # `sin_restriccion_especialidad` es True.
    especialidades: List[int] = Field(default_factory=list)


class NomencladorUpdate(BaseModel):
    # `descripcion` se distingue por `model_fields_set`: mandar "" la limpia.
    descripcion: Optional[str] = None
    categoria: Optional[str] = None
    complejidad: Optional[Literal["baja", "media", "alta"]] = None
    nomenclador_nacional_id: Optional[int] = None
    activo: Optional[bool] = None
    observacion: Optional[str] = None
    sin_restriccion_especialidad: Optional[bool] = None
    # None = no tocar la plantilla; lista (aunque vacía) = reemplazarla por completo.
    especialidades: Optional[List[int]] = None


class NomencladorOut(BaseModel):
    id: int
    codigo: str
    descripcion: Optional[str] = None
    categoria: Optional[str]
    complejidad: Optional[str]
    nomenclador_nacional_id: Optional[int] = None
    sin_restriccion_especialidad: Optional[bool] = None
    activo: bool
    observacion: Optional[str]
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = {"from_attributes": True}


class NomencladorDetalleOut(NomencladorOut):
    """`NomencladorOut` + la plantilla de especialidades sugeridas del código."""
    especialidades: List[int] = Field(default_factory=list)


class AplicarEspecialidadesIn(BaseModel):
    """Aplica la plantilla YA GUARDADA del código (especialidades o "sin restricción")
    a estas obras sociales. Se guarda primero, se aplica después."""
    obra_social_nros: List[int] = Field(min_length=1)


class AplicarEspecialidadesAplicadaOut(BaseModel):
    obra_social_nro: int
    variantes_creadas: int
    # Variantes que ya existían y no se tocaron.
    variantes_existentes: int = 0


class AplicarEspecialidadesOmitidaOut(BaseModel):
    obra_social_nro: int
    motivo: str


class AplicarEspecialidadesOut(BaseModel):
    aplicadas: List[AplicarEspecialidadesAplicadaOut]
    omitidas: List[AplicarEspecialidadesOmitidaOut]


# ─────────────────────────────────────────────────────────────────────────────
# NomencladorNacional
# ─────────────────────────────────────────────────────────────────────────────

class NomencladorNacionalCreate(BaseModel):
    codigo: str
    descripcion: Optional[str] = None
    unidades_honorarios: Optional[Decimal] = None
    unidades_ayudante: Optional[Decimal] = None
    unidades_gastos: Optional[Decimal] = None
    categoria: Optional[str] = None
    complejidad: Optional[Literal["baja", "media", "alta"]] = None


class NomencladorNacionalUpdate(BaseModel):
    descripcion: Optional[str] = None
    unidades_honorarios: Optional[Decimal] = None
    unidades_ayudante: Optional[Decimal] = None
    unidades_gastos: Optional[Decimal] = None
    categoria: Optional[str] = None
    complejidad: Optional[Literal["baja", "media", "alta"]] = None
    activo: Optional[bool] = None


class NomencladorNacionalOut(BaseModel):
    id: int
    codigo: str
    descripcion: Optional[str]
    unidades_honorarios: Optional[Decimal]
    unidades_ayudante: Optional[Decimal]
    unidades_gastos: Optional[Decimal]
    categoria: Optional[str]
    complejidad: Optional[str]
    activo: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# ValorEspecialidad — especialidades habilitadas por (obra_social_nro, código)
# ─────────────────────────────────────────────────────────────────────────────
#
# Sin CRUD propio: se editan enteramente desde el modal de Valores (`ValorUpdate.
# especialidades`, ver `routes_valores.update_valor_metadata`). `codigos_por_
# especialidad` (antes NomencladorEspecialidadResumenOut) sigue existiendo como
# consulta de solo lectura, ahora por OS.

class CodigoPorEspecialidadOut(BaseModel):
    """Fila código↔especialidad para una obra social puntual (vista de consulta)."""
    codigo: str
    descripcion: str
    especialidad_id_colegio: int
    especialidad: Optional[str] = None
    obra_social_nro: int

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
    visible: bool = True
    observacion: Optional[str]
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


class GalenoVisibilidadIn(BaseModel):
    """Muestra u oculta un galeno (todas sus filas: niveles y vigencias) de una OS
    en el boletín del médico. No afecta precios ni facturación."""
    obra_social_nro: int
    codigo: str
    visible: bool


class GalenoVisibilidadOut(BaseModel):
    obra_social_nro: int
    codigo: str
    visible: bool
    filas_actualizadas: int


# ─────────────────────────────────────────────────────────────────────────────
# GalenoPlantilla (solo lectura — se carga a mano por el programador)
# ─────────────────────────────────────────────────────────────────────────────

class GalenoPlantillaNivelOut(BaseModel):
    nivel: Optional[int]
    # Informativo: se seedea en 0, el precio real se pacta por OS al instanciar.
    valor_unitario: Decimal
    unidades_honorarios: Optional[Decimal]
    unidades_ayudante: Optional[Decimal]
    unidades_gastos: Optional[Decimal]

    model_config = {"from_attributes": True}


class GalenoPlantillaOut(BaseModel):
    """Un grupo de plantilla con sus niveles ya agrupados. La forma (`nombre` +
    `niveles[]`) calza casi 1:1 con GalenoCrearNivelesIn: el front solo agrega
    `obra_social_nro`, `vigencia_desde` y completa los `valor_unitario` reales."""
    grupo: str
    codigo: str
    nombre: str
    niveles: List[GalenoPlantillaNivelOut]


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


class GalenoLoteItem(BaseModel):
    """Un galeno del lote: plano (un nivel con `nivel=None`) o nivelado."""
    nombre: str
    niveles: List[GalenoNivelItem]


class GalenoImportarLoteIn(BaseModel):
    """Alta masiva desde una planilla. Todos comparten O.S. y vigencia."""
    obra_social_nro: int
    vigencia_desde: datetime.date
    galenos: List[GalenoLoteItem]
    observacion: Optional[str] = None
    # Si el galeno ya existe vigente: `omitir` lo saltea, `rotar` cierra el
    # vigente y abre uno nuevo desde `vigencia_desde`.
    si_existe: Literal["omitir", "rotar"] = "omitir"

    @model_validator(mode="after")
    def check_lote(self) -> "GalenoImportarLoteIn":
        if not self.galenos:
            raise ValueError("El lote no tiene galenos")
        for g in self.galenos:
            if not slugify_codigo(g.nombre):
                raise ValueError(f"Nombre inválido: '{g.nombre}' no genera una clave")
            if not g.niveles:
                raise ValueError(f"'{g.nombre}' no tiene niveles")
            vistos = set()
            for item in g.niveles:
                if item.nivel in vistos:
                    raise ValueError(f"'{g.nombre}': nivel {item.nivel} repetido")
                vistos.add(item.nivel)
        return self


class GalenoLoteResultItem(BaseModel):
    nombre: str
    codigo: str
    # creado | rotado | omitido | error
    estado: str
    niveles: int = 0
    detalle: Optional[str] = None


class GalenoImportarLoteResult(BaseModel):
    total: int
    creados: int
    rotados: int
    omitidos: int
    errores: int
    items: List[GalenoLoteResultItem]


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
    sin_restriccion: bool = False,
) -> None:
    """
    Reglas transversales por origen (válidas tanto en Pydantic como server-side):
    - NE exige especialidad_id_colegio (identifica qué especialidad habilitada cobra
      esta variante) SALVO que el par (OS, código) sea sin restricción de
      especialidad: ahí una NE sin especialidad es el precio para cualquier médico
      (ver service.lookup_precio). NN va siempre sin especialidad.
    - NN es siempre calculado: exige modalidad galeno y no puede ser por_presupuesto.

    Esta función solo valida forma (presencia/ausencia de especialidad). Que la
    especialidad esté efectivamente habilitada para el código se valida aparte, con
    consulta a la base, en ``service.validar_especialidad_habilitada``.

    `es_galeno`: True si la modalidad es calculable (galeno), False si fija, None si
    no aplica/desconocida (p.ej. por_presupuesto, donde no hay ecuación que evaluar).
    """
    if origen == Origen.NE.value and especialidad_id_colegio is None and not sin_restriccion:
        raise ValueError(
            "El origen NE exige especialidad_id_colegio, salvo que el código sea "
            "sin restricción de especialidad (sin_restriccion_especialidad=true)"
        )
    if origen != Origen.NE.value and especialidad_id_colegio is not None:
        raise ValueError("especialidad_id_colegio solo es válido para origen NE; NN debe ir sin especialidad")
    if origen == Origen.NN.value:
        if por_presupuesto:
            raise ValueError("El nomenclador nacional no admite precio por presupuesto.")
        if es_galeno is False:
            raise ValueError(
                "El origen NN exige modalidad galeno (todos los componentes calculables)"
            )


class CompletarBaseNNIn(BaseModel):
    obra_social_nro: int
    # True → calcula y devuelve lo que haría, sin guardar nada (vista previa).
    dry_run: bool = False


class GalenoBaseCreadoOut(BaseModel):
    codigo: str
    nombre: str


class GalenoBaseExistenteOut(BaseModel):
    codigo: str
    nombre: str
    valor_unitario: Decimal
    vigencia_desde: datetime.date


class CompletarBaseNNErrorOut(BaseModel):
    codigo: str
    motivo: str


class CompletarBaseNNOut(BaseModel):
    obra_social_nro: int
    dry_run: bool
    vigencia_desde: datetime.date
    galenos_creados: List[GalenoBaseCreadoOut]
    galenos_existentes: List[GalenoBaseExistenteOut]
    total_candidatos: int
    nn_creados: int
    nn_existentes: int
    habilitaciones_sembradas: int
    errores: List[CompletarBaseNNErrorOut]


VIGENCIA_ALTA_NE_CERO_DEFAULT = datetime.date(2026, 1, 1)


class AltaNECeroIn(BaseModel):
    codigo: str
    obra_social_nros: List[int] = Field(..., min_length=1)
    vigencia_desde: datetime.date = VIGENCIA_ALTA_NE_CERO_DEFAULT
    # True → calcula y devuelve lo que haría, sin guardar nada (vista previa).
    dry_run: bool = False


class EspecialidadNombreOut(BaseModel):
    id_colegio: int
    nombre: str


class AltaNECeroOSOut(BaseModel):
    obra_social_nro: int
    nombre: str
    # creada: se agregó al menos una variante · sin_cambios: ya tenía todas ·
    # omitida: no se pudo agregar sin tocar lo existente · error: falló.
    estado: Literal["creada", "sin_cambios", "omitida", "error"]
    # Especialidades (ID_COLEGIO_ESPE); None = la variante sin especialidad.
    creadas: List[Optional[int]]
    existentes: List[Optional[int]]
    motivo: Optional[str] = None
    # Ya tiene el código en NN con precio > 0: el NE en $0 le ganaría al cotizar.
    nn_con_precio: bool = False


class AltaNECeroOut(BaseModel):
    codigo: str
    descripcion: Optional[str]
    sin_restriccion: bool
    plantilla: List[EspecialidadNombreOut]
    vigencia_desde: datetime.date
    dry_run: bool
    total_creadas: int
    obras_sociales: List[AltaNECeroOSOut]


class ComponenteNNSugeridoOut(BaseModel):
    concepto: Literal["Honorarios", "Ayudante", "Gastos"]
    galeno_id: int
    galeno_nombre: str
    cantidad: Decimal
    orden: int


class ComponentesNNSugeridosOut(BaseModel):
    """Precarga del alta manual de un NN. `disponible=False` → `motivo` dice por qué
    (sin NN vinculado, fuera de rango, galeno base no vigente en la OS)."""
    disponible: bool
    motivo: Optional[str] = None
    componentes: List[ComponenteNNSugeridoOut] = []


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


def _rechazar_nne(v):
    """Compat de transición: NNE se eliminó, no se normaliza en silencio porque no
    hay especialidad con la que reconstruir la variante — hay que elegirla a mano."""
    if isinstance(v, str) and v.upper() == "NNE":
        raise ValueError(
            "El origen NNE fue eliminado: cargar como NE indicando la(s) especialidad(es) "
            "habilitadas para el código (ver POST /api/valores_nm/multi)"
        )
    return v


class ValorCreate(BaseModel):
    obra_social_nro: int
    nomenclador_id: int
    # Categoría/procedencia: fija la prioridad del lookup (NE > NN)
    origen: Origen
    # Obligatoria: cómo nombra ESTA obra social al código. Ya no hereda en silencio
    # del catálogo del Colegio — se carga acá, en el alta manual (a diferencia de la
    # rotación de precio y de los procesos masivos, que sí la heredan del valor
    # anterior/origen; ver actualizar_valor / replicar_*).
    descripcion: str = Field(..., min_length=1)
    nivel: Optional[int] = None
    complejidad: Optional[Literal["baja", "media", "alta"]] = None
    # Override de categoría por OS; NULL hereda la del nomenclador
    categoria: Optional[str] = None
    # Override por OS; NULL hereda nm_nomenclador.requiere_autorizacion. No es lo mismo
    # que False: False = "esta OS dice que no", NULL = "esta OS no opinó".
    requiere_autorizacion: Optional[bool] = None
    # Especialidad que cobra esta variante. Obligatoria en NE salvo que el código sea
    # sin restricción (ver sin_restriccion_especialidad abajo): ahí puede ir NULL y
    # es el precio para cualquier especialidad. Si viene, queda habilitada en
    # nm_valor_especialidad (ver validar_especialidad_habilitada). NN va siempre NULL.
    especialidad_id_colegio: Optional[int] = None
    # Dato del PAR (obra_social_nro, código): None = hereda lo que ya tenga el par;
    # True/False = lo fija y se propaga a todas sus filas activas.
    sin_restriccion_especialidad: Optional[bool] = None
    # True → código por presupuesto: se ignora la ecuación, los componentes H/G/A
    # se guardan en 0 y el monto lo informa la OS al facturar (modo manual)
    por_presupuesto: bool = False
    # Máximo de ayudantes admitidos para este código+OS. NULL = no lleva ayudantes.
    cantidad_ayudantes: Optional[int] = Field(None, ge=0)
    # Importe que el afiliado paga de su bolsillo por esta práctica en esta OS;
    # se descuenta del total a liquidar (ver facturacion.calcular_importe_total).
    coseguro: Decimal = Field(Decimal("0"), ge=0)
    vigencia_desde: datetime.date
    observacion: Optional[str] = None
    componentes: List[ValorComponenteIn] = []

    @field_validator("origen", mode="before")
    @classmethod
    def _origen_sin_nne(cls, v):
        return _rechazar_nne(v)

    @model_validator(mode="after")
    def check_componentes(self) -> "ValorCreate":
        es_galeno = None
        if not self.por_presupuesto:
            validar_lista_componentes(self.componentes)
            es_galeno = any(c.galeno_id is not None for c in self.componentes)
        # NE sin especialidad exige declararlo explícito: heredar "sin restricción"
        # del par se resuelve en la ruta, contra la base.
        validar_reglas_origen(
            self.origen.value, self.especialidad_id_colegio, self.por_presupuesto, es_galeno,
            sin_restriccion=bool(self.sin_restriccion_especialidad),
        )
        return self


class ValorCreateMulti(BaseModel):
    """Alta de una variante NE para varias especialidades a la vez: una fila NE por
    cada especialidad_id_colegio (mismo precio, misma vigencia). Cada una se valida
    contra el catálogo de habilitaciones del código igual que en ValorCreate."""
    obra_social_nro: int
    nomenclador_id: int
    origen: Literal[Origen.NE] = Origen.NE
    descripcion: str = Field(..., min_length=1)
    nivel: Optional[int] = None
    complejidad: Optional[Literal["baja", "media", "alta"]] = None
    categoria: Optional[str] = None
    requiere_autorizacion: Optional[bool] = None
    especialidades_id_colegio: List[int] = Field(..., min_length=1)
    # Dato del PAR — misma semántica que ValorCreate.sin_restriccion_especialidad.
    sin_restriccion_especialidad: Optional[bool] = None
    por_presupuesto: bool = False
    cantidad_ayudantes: Optional[int] = Field(None, ge=0)
    coseguro: Decimal = Field(Decimal("0"), ge=0)
    vigencia_desde: datetime.date
    observacion: Optional[str] = None
    componentes: List[ValorComponenteIn] = []

    @field_validator("especialidades_id_colegio")
    @classmethod
    def _sin_repetidos(cls, v: List[int]) -> List[int]:
        if len(set(v)) != len(v):
            raise ValueError("especialidades_id_colegio no puede tener valores repetidos")
        return v

    @model_validator(mode="after")
    def check_componentes(self) -> "ValorCreateMulti":
        if not self.por_presupuesto:
            validar_lista_componentes(self.componentes)
        return self

    def a_valor_create(self, especialidad_id_colegio: int) -> "ValorCreate":
        return ValorCreate(
            obra_social_nro=self.obra_social_nro,
            nomenclador_id=self.nomenclador_id,
            origen=self.origen,
            descripcion=self.descripcion,
            nivel=self.nivel,
            complejidad=self.complejidad,
            categoria=self.categoria,
            requiere_autorizacion=self.requiere_autorizacion,
            especialidad_id_colegio=especialidad_id_colegio,
            sin_restriccion_especialidad=self.sin_restriccion_especialidad,
            por_presupuesto=self.por_presupuesto,
            cantidad_ayudantes=self.cantidad_ayudantes,
            coseguro=self.coseguro,
            vigencia_desde=self.vigencia_desde,
            observacion=self.observacion,
            componentes=self.componentes,
        )


class ValorUpdate(BaseModel):
    # especialidad_id_colegio NO es editable: es la identidad de la variante
    #
    # descripcion / sin_restriccion_especialidad son datos del PAR (obra_social_nro,
    # código), no de esta fila puntual: al guardarlos se propagan a TODAS las filas
    # activas del mismo par (todas las variantes NE + la NN), para que no queden
    # desincronizadas entre sí — ver routes_valores.update_valor_metadata.
    descripcion: Optional[str] = None
    sin_restriccion_especialidad: Optional[bool] = None
    # Especialidades habilitadas para facturar este código EN esta obra social.
    # None = no tocar; [] = vaciar (rechazado si alguna tiene un Valor NE activo
    # dependiendo de ella); lista = REEMPLAZA por completo la habilitación actual del
    # par (no se suma). Es el único lugar del sistema donde se editan — ver
    # nm_valor_especialidad / service.reemplazar_especialidades.
    especialidades: Optional[List[int]] = None
    nivel: Optional[int] = None
    complejidad: Optional[Literal["baja", "media", "alta"]] = None
    categoria: Optional[str] = None
    requiere_autorizacion: Optional[bool] = None
    cantidad_ayudantes: Optional[int] = Field(None, ge=0)
    # coseguro NO va acá: es parte de la ecuación de precio, no un metadato — cambiarlo
    # cierra la vigencia actual y abre una nueva (ver ValorCerrarYCrearIn.coseguro).
    observacion: Optional[str] = None


class ValorNucleoUpdate(BaseModel):
    """Edición del "núcleo" de un código en una obra social: lo que vale PARA TODAS
    sus variantes por especialidad. Una sola transacción; ver `nucleo.py`.

    - Metadatos (`descripcion`, `nivel`, `complejidad`, `cantidad_ayudantes`,
      `observacion`): None = no tocar; si vienen, van a todas las variantes activas.
    - `ecuacion`: si viene, rota vigencia + componentes + coseguro de TODAS las
      variantes NE activas (mismo efecto que `aplicar_a_variantes=True`).
    - Especialidades: `sin_restriccion_especialidad` True deja UNA sola fila "sin
      especialidad" (cierra las demás); False + `especialidades` deja exactamente
      esa lista (crea las nuevas clonando la primera variante, cierra las sacadas).
    """
    descripcion: Optional[str] = None
    nivel: Optional[int] = None
    complejidad: Optional[Literal["baja", "media", "alta"]] = None
    cantidad_ayudantes: Optional[int] = Field(None, ge=0)
    observacion: Optional[str] = None
    ecuacion: Optional["ValorCerrarYCrearIn"] = None
    sin_restriccion_especialidad: bool = False
    especialidades: List[int] = Field(default_factory=list)
    # False = no tocar quién factura (ni crear ni cerrar variantes): el lápiz del
    # código sólo rota precios; quién factura se edita en el alta (etapa 3).
    tocar_especialidades: bool = True
    # Qué núcleo se edita cuando el código tiene NE y NN a la vez. NE = las variantes
    # por especialidad; NN = su fila única (mismo valor para cualquier especialidad).
    origen: Literal["NE", "NN"] = "NE"


class ValorCerrarYCrearIn(BaseModel):
    # La variante (especialidad_id_colegio) se conserva del valor que se cierra
    vigencia_desde: datetime.date
    componentes: List[ValorComponenteIn] = []
    por_presupuesto: bool = False
    descripcion: Optional[str] = None
    nivel: Optional[int] = None
    complejidad: Optional[Literal["baja", "media", "alta"]] = None
    categoria: Optional[str] = None
    requiere_autorizacion: Optional[bool] = None
    cantidad_ayudantes: Optional[int] = Field(None, ge=0)
    # None = hereda el coseguro del valor que se cierra (mismo patrón que
    # cantidad_ayudantes/categoria/requiere_autorizacion arriba).
    coseguro: Optional[Decimal] = Field(None, ge=0)
    observacion: Optional[str] = None
    # Si True, replica esta misma vigencia+componentes a las demás variantes NE
    # (mismas obra_social_nro + nomenclador_id, otra especialidad) activas al momento
    # de cerrar. No aplica a NN (no tiene hermanas por especialidad).
    aplicar_a_variantes: bool = False

    @model_validator(mode="after")
    def check_componentes(self) -> "ValorCerrarYCrearIn":
        if not self.por_presupuesto:
            validar_lista_componentes(self.componentes)
        return self


# ─────────────────────────────────────────────────────────────────────────────
# Replicar en obras sociales de la misma familia (planes de una misma empresa)
# ─────────────────────────────────────────────────────────────────────────────

class ReplicaAltaIn(BaseModel):
    """El mismo alta que se hizo en la OS de origen (ValorCreate / ValorCreateMulti)."""
    origen: Origen
    # Vacío = una sola fila sin especialidad (NN, o NE sin restricción).
    especialidades_id_colegio: List[int] = Field(default_factory=list)
    sin_restriccion_especialidad: Optional[bool] = None
    descripcion: str = Field(..., min_length=1)
    nivel: Optional[int] = None
    complejidad: Optional[Literal["baja", "media", "alta"]] = None
    categoria: Optional[str] = None
    requiere_autorizacion: Optional[bool] = None
    por_presupuesto: bool = False
    cantidad_ayudantes: Optional[int] = Field(None, ge=0)
    coseguro: Decimal = Field(Decimal("0"), ge=0)
    vigencia_desde: datetime.date
    observacion: Optional[str] = None
    componentes: List[ValorComponenteIn] = []


class ReplicaVarianteIn(BaseModel):
    """La misma rotación que se hizo sobre una variante en la OS de origen."""
    origen: Origen
    especialidad_id_colegio: Optional[int] = None
    ecuacion: ValorCerrarYCrearIn


class ReplicarFamiliaValorIn(BaseModel):
    origen_obra_social_nro: int
    nomenclador_id: int
    destinos: List[int] = Field(min_length=1)
    operacion: Literal["alta", "nucleo", "variante"]
    alta: Optional[ReplicaAltaIn] = None
    nucleo: Optional[ValorNucleoUpdate] = None
    variante: Optional[ReplicaVarianteIn] = None

    @model_validator(mode="after")
    def _payload_de_la_operacion(self) -> "ReplicarFamiliaValorIn":
        if getattr(self, self.operacion) is None:
            raise ValueError(f"Falta el payload de la operación '{self.operacion}'")
        return self


class ReplicaGalenoNivel(BaseModel):
    nivel: Optional[int] = None
    valor_unitario: Decimal
    unidades_honorarios: Optional[Decimal] = None
    unidades_ayudante: Optional[Decimal] = None
    unidades_gastos: Optional[Decimal] = None


class ReplicarFamiliaGalenoIn(BaseModel):
    origen_obra_social_nro: int
    destinos: List[int] = Field(min_length=1)
    operacion: Literal["alta", "precio", "unidades"]
    codigo: str
    vigencia_desde: datetime.date
    # alta: nombre + niveles creados en la OS de origen.
    nombre: Optional[str] = None
    niveles: List[ReplicaGalenoNivel] = Field(default_factory=list)
    # precio / unidades: el nivel editado.
    nivel: Optional[int] = None
    nuevo_valor_unitario: Optional[Decimal] = None
    # unidades: solo se aplican las que vienen en el body (mismo contrato que
    # actualizar_unidades: null limpia el default sin propagar).
    unidades_honorarios: Optional[Decimal] = None
    unidades_ayudante: Optional[Decimal] = None
    unidades_gastos: Optional[Decimal] = None

    @model_validator(mode="after")
    def _payload(self) -> "ReplicarFamiliaGalenoIn":
        if self.operacion == "alta" and (not self.nombre or not self.niveles):
            raise ValueError("El alta requiere nombre y niveles")
        if self.operacion == "precio" and self.nuevo_valor_unitario is None:
            raise ValueError("Actualizar valor requiere nuevo_valor_unitario")
        return self


class ReplicaResultadoItem(BaseModel):
    obra_social_nro: int
    nombre: str
    estado: Literal["replicado", "creado", "omitido", "error"]
    motivo: Optional[str] = None


class ReplicarFamiliaOut(BaseModel):
    resultados: List[ReplicaResultadoItem]


class ObraSocialFamiliaItem(BaseModel):
    nro_obra_social: int
    nombre: str
    es_principal: bool


class ValorOut(BaseModel):
    id: int
    obra_social_nro: int
    nomenclador_id: int
    origen: str
    codigo: str
    # Override de esta OS. NULL = hereda del catálogo — para MOSTRAR usar
    # `descripcion_efectiva`, que ya resuelve la herencia.
    descripcion: Optional[str]
    descripcion_efectiva: str = ""
    nivel: Optional[int]
    complejidad: Optional[str]
    categoria: Optional[str] = None
    requiere_autorizacion: Optional[bool] = None
    especialidad_id_colegio: Optional[int]
    por_presupuesto: bool = False
    sin_restriccion_especialidad: bool = False
    # Especialidades habilitadas HOY para (obra_social_nro, código) — dato del par,
    # igual para todas las variantes activas. Se resuelve aparte (no es columna de
    # Valor); ver routes_valores._valores_out.
    especialidades: List[int] = []
    cantidad_ayudantes: Optional[int] = None
    coseguro: Decimal = Decimal("0")
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
    # Aumentar los valores de precio fijo del origen/alcance de arriba.
    incluir_valores_fijos: bool = True
    # Códigos de galeno de la OS a aumentar (todos sus niveles vigentes). Eso
    # actualiza todos los valores calculables que los usan (NN y NE por galeno),
    # sin importar el origen ni el alcance de códigos. None/[] = no tocar galenos.
    galeno_codigos: Optional[List[str]] = None
    # True = vista previa: calcula lo mismo sin guardar nada.
    dry_run: bool = False

    @model_validator(mode="after")
    def _algo_que_aumentar(self) -> "ActualizarPorcentajeIn":
        if not self.incluir_valores_fijos and not self.galeno_codigos:
            raise ValueError("Elegí valores fijos, galenos o ambos")
        if self.porcentaje == 0:
            raise ValueError("El porcentaje no puede ser 0")
        return self


class AumentoDetalleItem(BaseModel):
    tipo: Literal["valor", "galeno"]
    codigo: str
    descripcion: Optional[str] = None
    especialidad_id_colegio: Optional[int] = None
    nivel: Optional[int] = None
    vigencia_actual: Optional[datetime.date] = None
    actual: Decimal
    nuevo: Optional[Decimal] = None
    # "actualiza" | "omitido" | "error"
    estado: Literal["actualiza", "omitido", "error"] = "omitido"
    motivo: Optional[str] = None


class AumentoPorcentualResult(BaseModel):
    actualizados: int              # valores fijos (o revertidos)
    galenos_actualizados: int = 0
    omitidos: int = 0
    errores: List[dict]
    detalle: List[AumentoDetalleItem]
    dry_run: bool = False


class ActualizarPorCodigosItem(BaseModel):
    nomenclador_id: int
    nuevo_valor_unitario: Decimal
    nuevo_nivel: Optional[int] = None
    # NULL = aplicar a TODAS las variantes NE activas de este código+OS (ex-NNE, mismo
    # precio para toda especialidad habilitada); con valor, aplica solo a esa variante.
    # No aplica a NN (siempre va sin especialidad).
    especialidad_id_colegio: Optional[int] = None


class ActualizarPorCodigosIn(BaseModel):
    obra_social_nro: int
    origen: Origen
    vigencia_desde: datetime.date
    items: List[ActualizarPorCodigosItem]


class RevertirActualizacionIn(BaseModel):
    obra_social_nro: int
    vigencia_revertir: datetime.date
    # True = vista previa de lo que se revertiría, sin guardar.
    dry_run: bool = False


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
    # códigos cuyo "quién puede cobrar" se sembró desde el catálogo (plantilla o sin
    # restricción) porque todavía no tenían nada configurado en esta OS
    habilitaciones_sembradas: int = 0
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
    via: Literal["T", "L"] = "T"              # T = tradicional, L = laparoscópica

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
    # Origen de la variante elegida (NE|NN) — la de mayor prioridad aplicable
    origen: str
    # Variante elegida: NULL = sin especialidad, N = variante por especialidad N
    variante_especialidad_id: Optional[int] = None
    # True → código por presupuesto: precio 0, el monto lo carga el operador a mano
    por_presupuesto: bool = False
    # True → la práctica necesita autorización previa de la OS. El front lo usa para
    # marcar el código y pedir el nro; el backend lo exige cuando carga el médico.
    requiere_autorizacion: bool = False
    fecha_practica: datetime.date
    precio_base: Decimal            # = precio_total (ya no hay opcionales)
    precio_total: Decimal           # suma de los 3 componentes
    # Importe que el afiliado paga de su bolsillo; sugerido desde el Valor, editable
    # al facturar. No está incluido en precio_total.
    coseguro: Decimal = Decimal("0")
    componentes: List[ComponenteLookupOut]
    # Vía cotizada (T=tradicional, L=laparoscópica) y, si L, el nivel efectivamente
    # usado para cotizar (galeno de 7 niveles → nivel siguiente; 10 niveles → mismo
    # nivel con recargo). Ver app/modules/nomenclador/service_vias.py.
    via: str = "T"
    nivel_cotizado: Optional[int] = None


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


class ValorSinHistorialOut(BaseModel):
    """Un `Valor` activo que no tiene ninguna fila en `nm_historial_precio_codigo`."""
    valor_id: int
    obra_social_nro: int
    nomenclador_id: int
    codigo: str
    origen: str
    especialidad_id_colegio: Optional[int] = None
    vigencia_desde: datetime.date


class SinHistorialPorObraSocialOut(BaseModel):
    obra_social_nro: int
    #: Valores activos huérfanos de esa obra social.
    valores: int
    #: Códigos distintos afectados (un código aporta un valor por especialidad).
    codigos: int
    vigencia_min: datetime.date
    vigencia_max: datetime.date


class DiagnosticoSinHistorialOut(BaseModel):
    """Resultado del chequeo de integridad valores ↔ historial.

    `total` tiene que ser **0**. Si no lo es, esos valores se ven en el panel
    pero son invisibles para `lookup_precio`, que cotiza sólo contra el
    historial: o la prestación se factura con otra variante (típicamente la NN,
    que gana por descarte) o queda sin precio. Ver
    `service.valores_activos_sin_historial`.
    """
    total: int
    por_obra_social: List[SinHistorialPorObraSocialOut]
    #: Muestra acotada por `limite_detalle`; `total` es el número real.
    detalle: List[ValorSinHistorialOut]


class ResumenVigenciaOut(BaseModel):
    """Una vigencia de una obra social, ya agregada: cuántos códigos entraron y
    cuánto varió el precio promedio contra la vigencia anterior de cada código.

    Sale de `nm_historial_precio_codigo` — la tabla materializada que el motor
    de valores ya mantiene en cada operación—, no de `nm_valores`: no hace
    falta traer la grilla completa (con sus componentes) para responder "cuándo
    y cuánto actualizó esta obra social". Ver auditoría H-01 / H-02.
    """
    vigencia_desde: datetime.date
    #: Códigos con una fila de historial en esta vigencia.
    cantidad: int
    #: Promedio de variación porcentual contra la versión anterior de cada
    #: código (mismo nomenclador_id + origen + especialidad). `None` cuando
    #: ningún código de esta vigencia tenía una versión previa — primera carga.
    avg_pct: Optional[float] = None


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
    # Sin esto, pedir el boletín de un código SIN obra social devuelve una
    # lista plana donde no se sabe a quién corresponde cada precio — que es
    # justo el caso que el endpoint dice soportar («el boletín las muestra
    # todas»). La columna ya estaba en la fila que se lee.
    obra_social_nro: int
    codigo: str
    origen: str
    descripcion: Optional[str]
    nivel: Optional[int]
    por_presupuesto: bool = False
    precio_total: Decimal
    componentes: List[BoletinComponenteOut]
    vigencia_desde: datetime.date
    vigencia_hasta: Optional[datetime.date]
    # De qué especialidad es el precio (ID_COLEGIO_ESPE); NULL = general. Un
    # mismo código puede tener precio de pediatría y precio general vigentes a
    # la vez, y sin esto el boletín no puede elegir el que corresponde.
    especialidad_id_colegio: Optional[int] = None


class BoletinOut(BaseModel):
    fecha: datetime.date
    obra_social_nro: Optional[int]
    items: List[BoletinItemOut]


class TablaValoresItem(BaseModel):
    nomenclador_id: int
    codigo: str
    origen: str
    # Especialidad de la variante elegida (ID_COLEGIO_ESPE). NULL = variante sin
    # perfil (NN). Se puebla al filtrar por especialidades.
    especialidad_id_colegio: Optional[int] = None
    descripcion: Optional[str]
    nivel: Optional[int]
    por_presupuesto: bool = False
    # Valor.sin_restriccion_especialidad de esta variante: el código lo puede
    # facturar cualquier especialidad en esta OS.
    sin_restriccion_especialidad: bool = False
    precio_total: Decimal
    vigencia_desde: datetime.date
    vigencia_hasta: Optional[datetime.date]
    componentes: list
    # Vía efectivamente aplicada al precio de esta fila (T=tradicional, L=laparoscópica).
    # Si se pidió L y el código no admite laparoscopía, la fila cae a T con su precio
    # tradicional en vez de romper el listado (ver GET /tabla_valores).
    via_aplicada: str = "T"


class EvolucionPrecioItem(BaseModel):
    vigencia_desde: datetime.date
    vigencia_hasta: Optional[datetime.date]
    precio_total: Decimal
    motivo_cambio: str
    fecha_cambio: datetime.datetime


# ─────────────────────────────────────────────────────────────────────────────
# Etapa 3 — código dado de alta en una obra social (nm_codigo_obra_social)
# ─────────────────────────────────────────────────────────────────────────────

EstadoCodigoOS = Literal["sin_alta", "sin_precio", "con_precio", "suspendido"]
Complejidad = Literal["baja", "media", "alta"]


class AltaCodigoItem(BaseModel):
    """Un (obra social, código) a dar de alta. Lo no informado se toma del catálogo:
    descripción del Colegio y su plantilla de especialidades / "sin restricción"."""
    obra_social_nro: int
    nomenclador_id: int
    descripcion: Optional[str] = Field(None, max_length=255)
    # None = usar la plantilla del Colegio (si el par todavía no tiene especialidades
    # configuradas). Lista (aunque vacía) = reemplazar por esa lista.
    especialidades: Optional[List[int]] = None
    sin_restriccion_especialidad: Optional[bool] = None


class AltaCodigosIn(BaseModel):
    items: List[AltaCodigoItem] = Field(..., min_length=1)
    # Condiciones comunes a todos los items.
    requiere_autorizacion: Optional[bool] = None
    cantidad_ayudantes: Optional[int] = Field(None, ge=0)


class AltaCodigoResultado(BaseModel):
    obra_social_nro: int
    nomenclador_id: int
    codigo: str
    estado: Literal["creado", "reactivado", "ya_existia", "error"]
    motivo: Optional[str] = None
    # True si quedó dado de alta pero nadie puede facturarlo (sin especialidades
    # y sin "sin restricción").
    sin_quien_factura: bool = False


class AltaCodigosOut(BaseModel):
    resultados: List[AltaCodigoResultado]


class CodigoObraSocialUpdate(BaseModel):
    """Datos del par editables en la etapa 3. Sólo se aplica lo que viene."""
    descripcion: Optional[str] = Field(None, max_length=255)
    categoria: Optional[str] = Field(None, max_length=100)
    complejidad: Optional[Complejidad] = None
    requiere_autorizacion: Optional[bool] = None
    cantidad_ayudantes: Optional[int] = Field(None, ge=0)
    observacion: Optional[str] = None
    sin_restriccion_especialidad: Optional[bool] = None
    especialidades: Optional[List[int]] = None
    # Si quitar especialidades (o "sin restricción") deja precios activos sin con qué
    # cotizar: False → 409 `precios_dependientes` para que la pantalla pregunte;
    # True → esos precios se cierran con vigencia hasta ayer.
    cerrar_precios: bool = False


class CodigoObraSocialOut(BaseModel):
    obra_social_nro: int
    nomenclador_id: int
    codigo: str
    descripcion: Optional[str] = None
    descripcion_colegio: Optional[str] = None
    categoria: Optional[str] = None
    complejidad: Optional[str] = None
    requiere_autorizacion: Optional[bool] = None
    cantidad_ayudantes: Optional[int] = None
    observacion: Optional[str] = None
    sin_restriccion_especialidad: bool = False
    especialidades: List[int] = []
    estado: EstadoCodigoOS
    tiene_precio: bool = False


class CodigoPorOSItem(BaseModel):
    """Fila de "Códigos por obra social": un código del catálogo y su estado en la O.S."""
    nomenclador_id: int
    codigo: str
    descripcion_colegio: Optional[str] = None
    descripcion_os: Optional[str] = None
    estado: EstadoCodigoOS
    sin_restriccion_especialidad: bool = False
    especialidades_os: int = 0
    especialidades_plantilla: int = 0
    plantilla_sin_restriccion: bool = False


class CodigosPorOSOut(BaseModel):
    obra_social_nro: int
    total: int
    page: int
    size: int
    conteos: dict[str, int]
    items: List[CodigoPorOSItem]


class FichaObraSocialItem(BaseModel):
    obra_social_nro: int
    nombre: str
    estado: EstadoCodigoOS
    sin_restriccion_especialidad: bool = False
    especialidades: int = 0
    # Resumen del precio vigente hoy: "igual" (una sola variante para todas las
    # especialidades) con su total, o "por_especialidad" con la cantidad.
    precio_tipo: Optional[Literal["igual", "por_especialidad"]] = None
    precio_total: Optional[Decimal] = None
    variantes: int = 0
    vigencia_desde: Optional[datetime.date] = None
    prestaciones_sin_valorizar: int = 0


class FichaCodigoOut(BaseModel):
    nomenclador_id: int
    codigo: str
    descripcion: Optional[str] = None
    categoria: Optional[str] = None
    complejidad: Optional[str] = None
    activo: bool = True
    plantilla_especialidades: List[int] = []
    plantilla_sin_restriccion: bool = False
    conteos: dict[str, int]
    obras_sociales: List[FichaObraSocialItem]


# ─── Etapa 2 — plantilla de especialidades (quién factura) ───────────────────

class PlantillaEspecialidadesIn(BaseModel):
    sin_restriccion_especialidad: bool = False
    especialidades: List[int] = []


class PlantillaEspecialidadesOut(BaseModel):
    nomenclador_id: int
    codigo: str
    sin_restriccion_especialidad: bool
    especialidades: List[int]


class PropagarEspecialidadesIn(BaseModel):
    obra_social_nros: List[int] = Field(..., min_length=1)
    # agregar = sólo suma lo nuevo de la plantilla; igualar = además quita lo que
    # la O.S. tenga de más (salvo especialidades con precio NE activo).
    modo: Literal["agregar", "igualar"] = "agregar"
    dry_run: bool = False


class PropagarEspecialidadesItem(BaseModel):
    obra_social_nro: int
    nombre: str
    estado: Literal["actualizada", "sin_cambios", "salteada", "error"]
    motivo: Optional[str] = None
    agrega: List[int] = []
    quita: List[int] = []
    # Se quitarían pero tienen precio NE activo: se quedan.
    conserva_por_precio: List[int] = []


class PropagarEspecialidadesOut(BaseModel):
    dry_run: bool
    modo: Literal["agregar", "igualar"]
    resultados: List[PropagarEspecialidadesItem]


class AplicarAltaIn(BaseModel):
    obra_social_nros: List[int] = Field(..., min_length=1)


class AplicarAltaItem(BaseModel):
    obra_social_nro: int
    nombre: str
    estado: Literal["alta_creada", "especialidades_agregadas", "sin_cambios", "error"]
    motivo: Optional[str] = None
    especialidades_agregadas: List[int] = []
    sin_quien_factura: bool = False


class AplicarAltaOut(BaseModel):
    resultados: List[AplicarAltaItem]


# ─────────────────────────────────────────────────────────────────────────────
# Nomencladores nivelados (Cirugía adulto 7/10, Cirugía infantil, FASGO, Urología…)
# ─────────────────────────────────────────────────────────────────────────────

class NomencladorNiveladoOut(BaseModel):
    id: int
    slug: str
    nombre: str
    galeno_grupo: str
    galeno_codigo: Optional[str] = None
    galeno_nombre: Optional[str] = None
    niveles: int
    total_codigos: int
    por_nivel: dict[int, int]
    con_unidades: int


class NiveladoCodigoOut(BaseModel):
    nomenclador_id: int
    codigo: str
    descripcion: Optional[str] = None
    activo: bool
    nivel: Optional[int] = None
    unidades: Optional[Decimal] = None
    observacion: Optional[str] = None


class NiveladoCodigosOut(BaseModel):
    items: List[NiveladoCodigoOut]
    total: int
    page: int
    size: int


class NiveladoCodigoIn(BaseModel):
    """Nivel o unidades fijas: exactamente uno."""
    nivel: Optional[int] = Field(None, ge=1)
    unidades: Optional[Decimal] = Field(None, gt=0)
    observacion: Optional[str] = None

    @model_validator(mode="after")
    def _uno_de_los_dos(self) -> "NiveladoCodigoIn":
        if (self.nivel is None) == (self.unidades is None):
            raise ValueError("Indicá el nivel o las unidades fijas (uno de los dos).")
        return self


class NiveladoCodigoAltaIn(NiveladoCodigoIn):
    nomenclador_id: int


class AplicarNiveladoIn(BaseModel):
    obra_social_nro: int
    vigencia_desde: datetime.date
    dry_run: bool = True


EstadoAplicarNivelado = Literal[
    "crear", "creado", "ya_tiene_precio", "sin_quien_factura", "suspendido", "omitido",
]


class AplicarNiveladoFila(BaseModel):
    nomenclador_id: int
    codigo: str
    descripcion: Optional[str] = None
    nivel: Optional[int] = None
    unidades: Optional[Decimal] = None
    estado: EstadoAplicarNivelado
    # Cuántos precios (uno por especialidad, o uno "sin restricción").
    precios: int = 0
    # Honorarios + ayudante + gastos con el galeno de la O.S. (orientativo).
    precio: Optional[Decimal] = None
    motivo: Optional[str] = None


class AplicarNiveladoResumen(BaseModel):
    total: int
    crear: int
    ya_tiene_precio: int
    sin_quien_factura: int
    suspendido: int
    omitido: int
    precios: int
    altas: int


class AplicarNiveladoOut(BaseModel):
    dry_run: bool
    obra_social_nro: int
    nomenclador: str
    galeno_nombre: str
    resumen: AplicarNiveladoResumen
    filas: List[AplicarNiveladoFila]
