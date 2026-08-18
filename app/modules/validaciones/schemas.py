"""Schemas del módulo de validaciones con obras sociales."""
import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# `cargada` = la prestación se autorizó por fuera y acá sólo se registró
# (Boreal, Omint). Los otros tres salen de la respuesta del validador.
EstadoPrestacion = Literal["autorizada", "rechazada", "pendiente", "cargada"]


class PrestacionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    # `mes`/`anio` salen del `periodo` YYYYMM de la fila.
    fecha: Optional[datetime.date]
    codigo: str
    descripcion: str
    nro_afiliado: str
    nombre_afiliado: str
    nro_validacion: Optional[str]
    estado: EstadoPrestacion
    estado_detalle: str
    cantidad: int
    honorarios: Decimal
    gastos: Decimal
    coseguro: Decimal
    total: Decimal
    mes: int
    anio: int
    obra_social: int
    # Ruta de la orden/receta adjunta (columna NOMBRE_ARCHIVO), si tiene.
    orden: Optional[str] = None


class EntradaBase(BaseModel):
    """Lo que toda obra social necesita sí o sí, ya validado.

    Cada obra social integrada declara su propia subclase (`obras/<os>/schemas.py`)
    con los campos y reglas de formato que le hacen falta a ella —
    `core/entrada.py::parsear_entrada()` construye la subclase correcta a partir
    del `PrestacionCreate` que mandó el front.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    # El código que tipeó el médico. El que efectivamente se cotiza y se graba
    # sale de `ResultadoValidacion.codigo` — algunas O.S. (Sancor) lo sustituyen.
    codigo: str = Field(..., min_length=1, max_length=20)
    cantidad: int = Field(1, ge=1, le=999)


class EntradaManual(EntradaBase):
    """Boreal y Omint: la obra social ya autorizó por fuera del panel, acá
    sólo se registra el resultado."""

    nombre_afiliado: str = Field("", max_length=100)
    nro_afiliado: str = Field("", max_length=30)
    nro_validacion: str = Field("", max_length=30)
    coseguro: Decimal = Field(Decimal("0"), ge=0)


class PrestacionCreate(BaseModel):
    """Alta de prestación.

    Según la obra social el backend resuelve solo si es carga manual (Boreal,
    Omint) o si hay que pedir la autorización en línea (Sancor).
    """

    obra_social: int = Field(..., description="NRO_OBRASOCIAL del Colegio")
    codigo: str = Field(..., min_length=1, max_length=20)
    nombre_afiliado: str = Field("", max_length=100)
    nro_afiliado: str = Field("", max_length=30)
    # Dígito verificador / orden familiar. Sancor lo manda por separado.
    barra_afiliado: str = Field("", max_length=2)
    # Número de autorización ya obtenido (sólo carga manual).
    nro_validacion: str = Field("", max_length=30)
    # Token de la credencial — obligatorio en las obras sociales en línea.
    token: str = Field("", max_length=8)
    coseguro: Decimal = Field(Decimal("0"), ge=0)
    cantidad: int = Field(1, ge=1, le=999)
    # Sólo lo puede mandar el personal del Colegio cargando por un médico.
    nro_socio: Optional[int] = None


class PeriodoOut(BaseModel):
    mes: int
    anio: int
    cantidad: int
    total: Decimal
    cerrado: bool = False


class PeriodoActualOut(BaseModel):
    mes: int
    anio: int
    cerrado: bool = False


class CodigoOut(BaseModel):
    codigo: str
    descripcion: str
    honorarios: Decimal
    gastos: Decimal
    total: Decimal
    # `False` cuando el código no está habilitado para el médico o no tiene
    # precio vigente para esa obra social; `motivo` explica por qué.
    admitido: bool = True
    motivo: Optional[str] = None


class PrestadorOut(BaseModel):
    nro_socio: int
    nombre: str
    matricula: int
    categoria: str
