"""Schemas del panel de pre-exportación (detalle + carátula de una factura).

Separado de `facturacion/schemas.py` porque es un dominio propio (opciones de
render, no de negocio) que además alimenta la tabla de presets.
"""
import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

OrdenExport = Literal[
    "nombre_socio", "nro_socio", "fecha_practica", "fecha_carga", "codigo",
    "importe", "importe_desc", "nombre_afiliado", "especialidad",
]

# Sentido del criterio de orden (igual que la vista del listado). `importe_desc` es el
# valor histórico (siempre de mayor a menor) y no depende de esto.
DireccionExport = Literal["asc", "desc"]

# "por_tipo", dentro de Honorarios individuales y de Sanatorios: "medico" deja el orden de
# siempre (el de `orden`/`direccion`); "paciente" ordena los pacientes A-Z.
OrdenAlfabeticoExport = Literal["medico", "paciente"]

AgrupacionExport = Literal["todo_junto", "por_tipo", "por_socio", "plana"]

# Columnas disponibles para el detalle. `socio`/`sub_total`/`tipo` son siempre
# obligatorias (identifican la fila y cierran el resumen) — no forman parte del
# selector, así que no están acá.
ColumnaExport = Literal[
    "prestador", "obra_social", "matricula", "autorizacion", "fecha", "codigo",
    "nro_afiliado", "afiliado", "cantidad", "porcentaje",
    "honorarios", "gastos", "coseguro", "valor_unitario", "diagnostico", "via", "especialidad",
    "estado_validacion",
]

COLUMNAS_DEFAULT: list[ColumnaExport] = [
    "prestador", "matricula", "autorizacion", "fecha", "codigo",
    "nro_afiliado", "afiliado", "cantidad", "porcentaje",
    "honorarios", "gastos",
]

# "Detalle por médico" cruza todas las obras sociales de un mismo socio (ver
# `datos.obtener_filas_export_por_medico`): el encabezado ya identifica al
# médico (`construir_encabezado_por_medico`), así que repetir "prestador" en
# cada fila es ruido — lo que hace falta por fila es de qué obra social es, y
# el coseguro, que no venía en el default genérico.
COLUMNAS_DEFAULT_POR_MEDICO: list[ColumnaExport] = [
    "obra_social", "matricula", "autorizacion", "fecha", "codigo",
    "nro_afiliado", "afiliado", "cantidad", "porcentaje",
    "honorarios", "gastos", "coseguro",
]

TipoPrestacion = Literal["Consulta", "Practica", "Honorarios individuales", "Sanatorio"]


class ExportOpciones(BaseModel):
    """Opciones del panel — llegan como query params (ver `routes.py::_parse_opciones`)
    o resueltas desde un preset guardado."""

    orden: OrdenExport = "nombre_socio"
    direccion: DireccionExport = "asc"
    agrupacion: AgrupacionExport = "todo_junto"
    # Como la opción "Agrupar equipo" de la vista: el equipo (ayudante/gastos/pediatra) va
    # ÚNICAMENTE bajo la fila de su cirujano y cuenta en su grupo. Apagado, cada integrante
    # es una línea común de su propio socio.
    agrupar_equipo: bool = True
    orden_honorarios: OrdenAlfabeticoExport = "medico"
    orden_sanatorio: OrdenAlfabeticoExport = "medico"

    columnas: list[ColumnaExport] = Field(default_factory=lambda: list(COLUMNAS_DEFAULT))

    # Filtros — todos opcionales, se aplican con AND.
    fecha_desde: Optional[datetime.date] = None
    fecha_hasta: Optional[datetime.date] = None
    id_especialidad: Optional[int] = None
    cod_medicos: Optional[list[str]] = None  # subset de socios/prestadores
    revisado: Optional[bool] = None
    tipos: Optional[list[TipoPrestacion]] = None


# "vista": configuración de la VISTA del listado (agrupación, orden, filtros, columnas…).
# El export sale como se ve, así que los presets se guardan por vista. "detalle" es el
# formato anterior (opciones del panel de exportar) y se conserva sólo para lo ya guardado.
TipoDocumentoPreset = Literal["detalle", "caratula", "vista"]

# Claves que guarda un preset de vista (espejo de `VistaOpciones` del front).
CLAVES_PRESET_VISTA = {
    "orden", "direccion", "agrupacion", "agruparEquipo", "ordenHonorarios", "ordenSanatorio", "columnas",
    "fecha_desde", "fecha_hasta", "id_especialidad", "cod_medicos", "revisado", "tipos",
}


class PresetIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=120)
    tipo_documento: TipoDocumentoPreset
    opciones: dict[str, Any]

    @model_validator(mode="after")
    def _validar_opciones(self):
        if self.tipo_documento == "detalle":
            self.opciones = ExportOpciones(**self.opciones).model_dump(mode="json")
        elif self.tipo_documento == "vista":
            self.opciones = {k: v for k, v in self.opciones.items() if k in CLAVES_PRESET_VISTA}
        return self


class PresetOut(BaseModel):
    id: int
    nombre: str
    tipo_documento: TipoDocumentoPreset
    opciones: dict[str, Any]
    created_at: datetime.datetime

    class Config:
        from_attributes = True
