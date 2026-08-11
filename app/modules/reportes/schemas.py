"""Schemas de Reportes y Estadísticas.

Todas las filas son AGREGADOS: nunca se devuelve la tabla cruda salvo en el
listado por obra social, que es el único caso donde el operador necesita ver
prestación por prestación (y va paginado).
"""
import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, field_serializer


class _Money(BaseModel):
    """Serializa los Decimal como float, igual que el resto del panel."""

    @field_serializer("*", when_used="json")
    def _ser(self, v):
        return float(v) if isinstance(v, Decimal) else v


class ResumenOut(_Money):
    periodo: str
    prestaciones: int
    importe_total: Decimal
    honorarios: Decimal
    gastos: Decimal
    medicos: int
    obras_sociales: int
    codigos: int


class CodigoStatOut(_Money):
    codigo: str
    descripcion: Optional[str] = None
    cantidad: int
    prestaciones: int
    importe_total: Decimal
    medicos: int


class MedicoStatOut(_Money):
    nro_socio: str
    nombre: Optional[str] = None
    prestaciones: int
    cantidad: int
    importe_total: Decimal


class ObraSocialStatOut(_Money):
    obra_social_nro: str
    nombre: Optional[str] = None
    prestaciones: int
    importe_total: Decimal
    medicos: int


class PrestacionOut(_Money):
    id: int
    fecha: Optional[datetime.date] = None
    periodo: str
    codigo: Optional[str] = None
    descripcion: Optional[str] = None
    nro_socio: str
    medico: Optional[str] = None
    afiliado: Optional[str] = None
    nro_afiliado: Optional[str] = None
    cantidad: int
    importe_total: Decimal
    autorizacion: Optional[str] = None
    # NULL = la fila no pasó por el módulo de validaciones (carga del Colegio).
    validacion_estado: Optional[str] = None


class PaginaPrestaciones(BaseModel):
    items: List[PrestacionOut]
    total: int


class PuntoSerieOut(_Money):
    periodo: str
    prestaciones: int
    importe_total: Decimal
