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

    # Socio elegido a mano en la previsualización (matrícula repetida o sin
    # socio). Sólo lo manda Prevención; Swiss sigue descartando esas filas.
    nro_socio_elegido: Optional[int] = None


class ImportacionIn(BaseModel):
    """Lo que manda el panel, tanto para previsualizar como para confirmar."""

    # `None` → se usa el puntero `periodo_medico_actual` de la obra social.
    periodo: Optional[str] = Field(None, pattern=r"^\d{6}$")
    archivo: str = ""
    filas: List[FilaReporte]


class ImportacionPrevencionIn(ImportacionIn):
    # 103 (Prevención Salud) o 888, la obra social de prueba con el mismo
    # nomenclador. El servicio rechaza cualquier otra.
    obra_social: int = 103


class FilaUnne(BaseModel):
    """Una fila del Excel que exporta el sistema de UNNE, tal como la leyó el front.

    El grano es el ítem de la orden: una orden (`orden`) puede traer varias prácticas,
    numeradas en `reg` (1, 2, 3…), y cada una es una prestación aparte.
    """

    referencia: str = ""        # N° Referencia — interna de UNNE, sólo se muestra
    orden: str = ""             # Orden N° → `autorizacion`
    reg: int = 1                # Reg. — ítem dentro de la orden
    matricula: str = ""
    prestador: str = ""
    periodo_archivo: str = ""   # Periodo del Excel (AAAAMM), para avisar si no coincide
    provincia: str = ""
    fecha: Optional[datetime.date] = None
    cantidad: int = 1
    codigo: str = ""
    descripcion: str = ""
    funcion: str = ""           # ESPECIALISTA | Hon+Gto
    porcentaje: int = 100       # sacado de "Norma" ("Sin norma (449) - 100%")
    dni: str = ""
    paciente: str = ""
    importe: Decimal = Decimal("0.00")  # total de la línea (ya multiplicado por cantidad)
    # Socio elegido a mano en la previsualización (matrícula sin socio o repetida).
    nro_socio_elegido: Optional[int] = None


class ImportacionUnneIn(BaseModel):
    periodo: Optional[str] = Field(None, pattern=r"^\d{6}$")
    archivo: str = ""
    filas: List[FilaUnne]


class CandidatoSocio(BaseModel):
    nro_socio: int
    nombre: str = ""


class FilaResultado(BaseModel):
    """Qué pasaría (o pasó) con una fila."""

    indice: int
    # grabable | omitida | duplicada | grabada | elegir_socio (sólo UNNE)
    resultado: str
    motivo: str = ""
    # Advertencia que no impide grabar (ej. importe distinto al del nomenclador).
    aviso: str = ""
    # Socios posibles cuando la matrícula está repetida o no cae en ninguno (UNNE).
    candidatos: List[CandidatoSocio] = []
    reg: Optional[int] = None

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
    # Sólo UNNE: filas que esperan que se elija el socio, y filas con aviso.
    por_elegir: int = 0
    con_aviso: int = 0


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
