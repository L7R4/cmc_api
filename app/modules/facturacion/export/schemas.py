"""Schemas del panel de pre-exportación (detalle + carátula de una factura).

Separado de `facturacion/schemas.py` porque es un dominio propio (opciones de
render, no de negocio) que además alimenta la tabla de presets.
"""
import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

OrdenExport = Literal[
    "nombre_socio", "nro_socio", "fecha_practica", "codigo",
    "importe_desc", "nombre_afiliado", "especialidad",
]

AgrupacionExport = Literal["todo_junto", "por_tipo", "por_socio", "plana"]

# Columnas disponibles para el detalle. `socio`/`sub_total`/`tipo` son siempre
# obligatorias (identifican la fila y cierran el resumen) — no forman parte del
# selector, así que no están acá.
ColumnaExport = Literal[
    "prestador", "matricula", "autorizacion", "fecha", "codigo",
    "nro_afiliado", "afiliado", "cantidad", "porcentaje",
    "honorarios", "gastos", "coseguro", "diagnostico", "via", "especialidad",
    "estado_validacion",
]

COLUMNAS_DEFAULT: list[ColumnaExport] = [
    "prestador", "matricula", "autorizacion", "fecha", "codigo",
    "nro_afiliado", "afiliado", "cantidad", "porcentaje",
    "honorarios", "gastos",
]

TipoPrestacion = Literal["Consulta", "Practica", "Honorarios individuales", "Sanatorio"]


class ExportOpciones(BaseModel):
    """Opciones del panel — llegan como query params (ver `routes.py::_parse_opciones`)
    o resueltas desde un preset guardado."""

    orden: OrdenExport = "nombre_socio"
    agrupacion: AgrupacionExport = "todo_junto"

    columnas: list[ColumnaExport] = Field(default_factory=lambda: list(COLUMNAS_DEFAULT))

    # Filtros — todos opcionales, se aplican con AND.
    fecha_desde: Optional[datetime.date] = None
    fecha_hasta: Optional[datetime.date] = None
    id_especialidad: Optional[int] = None
    cod_medicos: Optional[list[str]] = None  # subset de socios/prestadores
    revisado: Optional[bool] = None
    tipos: Optional[list[TipoPrestacion]] = None


class PresetIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=120)
    tipo_documento: Literal["detalle", "caratula"]
    opciones: ExportOpciones


class PresetOut(BaseModel):
    id: int
    nombre: str
    tipo_documento: Literal["detalle", "caratula"]
    opciones: ExportOpciones
    created_at: datetime.datetime

    class Config:
        from_attributes = True
