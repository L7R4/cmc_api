import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field


class FilaReporte(BaseModel):
    """Una práctica del reporte, tal como la leyó el front.

    El grano es la práctica, no la autorización: una autorización de Prevención
    puede traer varias prácticas en la misma celda y cada una se factura por
    separado.
    """

    nro_autorizacion: str = ""
    fecha: Optional[datetime.date] = None
    afiliado: str = ""
    profesional: str = ""
    matricula: str = ""
    conformidad: str = ""
    codigo: str = ""
    descripcion: str = ""
    estado: str = ""

    # Los dos siguientes sólo los trae Swiss Medical; Prevención no informa ni
    # el número de afiliado ni el copago, y ahí quedan en su valor por defecto.
    nro_afiliado: str = ""
    cantidad: int = 1
    coseguro: Decimal = Decimal("0.00")


class ImportacionIn(BaseModel):
    """Lo que manda el panel, tanto para previsualizar como para confirmar."""

    # `None` → se usa el puntero `periodo_medico_actual` de la obra social.
    periodo: Optional[str] = Field(None, pattern=r"^\d{6}$")
    archivo: str = ""
    filas: List[FilaReporte]


class FilaResultado(BaseModel):
    """Qué pasaría (o pasó) con una fila."""

    indice: int
    # grabable | omitida | duplicada | grabada
    resultado: str
    motivo: str = ""

    nro_autorizacion: str = ""
    fecha: Optional[datetime.date] = None
    codigo: str = ""
    descripcion: str = ""
    afiliado: str = ""
    estado_reporte: str = ""

    matricula: str = ""
    # Resueltos contra `listado_medico`; vacíos si la matrícula no cayó.
    nro_socio: Optional[int] = None
    medico: str = ""

    honorarios: Decimal = Decimal("0.00")
    gastos: Decimal = Decimal("0.00")
    importe_total: Decimal = Decimal("0.00")
    # 'A' entra a la factura, 'X' queda fuera (rechazada por la obra social).
    estado_detalle: str = ""

    # Sólo en `confirmar`.
    id_detalle: Optional[int] = None


class ResumenImportacion(BaseModel):
    periodo: str
    total: int
    grabables: int
    duplicadas: int
    omitidas: int
    sin_medico: int
    rechazadas: int
    importe_total: Decimal = Decimal("0.00")


class ImportacionOut(BaseModel):
    resumen: ResumenImportacion
    filas: List[FilaResultado]


class PeriodoOpcion(BaseModel):
    periodo: str
    # El Colegio ya cerró o liquidó ese período: no admite carga.
    cerrado: bool
    # El que apunta `periodo_medico_actual`; el panel lo preselecciona.
    sugerido: bool


class PeriodosOut(BaseModel):
    sugerido: str
    periodos: List[PeriodoOpcion]
