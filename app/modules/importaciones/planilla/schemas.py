import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field

from app.modules.importaciones.schemas import CandidatoSocio, PeriodosOut


class FilaPlanilla(BaseModel):
    """Una fila de la planilla tal como la leyó el front.

    Todo llega como texto de la celda: el front no interpreta nada (ni fechas ni
    números), así que las reglas viven una sola vez, en el servicio. La fecha de una
    celda de Excel llega ya en ISO (`2026-09-08`); el texto, tal cual lo escribieron.
    """

    fila: int = 0                      # número de fila en el Excel, sólo para mostrar
    tipo: str = ""
    nro_socio: str = ""
    matricula: str = ""
    clinica: str = ""
    fecha: str = ""
    autorizacion: str = ""
    codigo: str = ""
    via: str = ""
    cantidad: str = ""
    sesion: str = ""
    porcentaje: str = ""
    nro_afiliado: str = ""
    afiliado: str = ""
    calculo: str = ""
    honorarios: str = ""
    gastos: str = ""
    ayudante: str = ""
    coseguro: str = ""
    equipo: str = ""
    rol: str = ""
    comentarios: str = ""

    # Decisiones del operador en la previsualización.
    nro_socio_elegido: Optional[int] = None
    descartada: bool = False


class PlanillaIn(BaseModel):
    obra_social: int
    periodo: str = Field(..., pattern=r"^\d{6}$")
    archivo: str = ""
    # Tope por cuerpo: el JSON de una fila pesa unos 300–500 bytes y el límite de la API
    # es de 2 MB (`MAX_JSON_BODY_BYTES`).
    filas: List[FilaPlanilla] = Field(..., max_length=5000)
    # Lo genera el front al confirmar. Identifica la importación para seguir su avance,
    # cancelarla y, si hace falta, saber después qué llegó a quedar grabado.
    lote: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F-]{8,40}$")


class FilaPlanillaResultado(BaseModel):
    indice: int
    fila: int = 0
    # grabable | grabada | duplicada | omitida | elegir_socio | descartada
    resultado: str
    motivo: str = ""
    aviso: str = ""
    candidatos: List[CandidatoSocio] = []

    tipo: str = ""                      # tipo con el que se carga (el que derivó el sistema)
    equipo: str = ""
    rol: str = ""
    nro_socio: Optional[int] = None
    medico: str = ""
    matricula: str = ""
    codigo: str = ""
    descripcion: str = ""
    fecha: Optional[datetime.date] = None
    afiliado: str = ""
    cantidad: int = 1
    importe_total: Decimal = Decimal("0.00")
    comentarios: str = ""
    id_detalle: Optional[int] = None


class ResumenPlanilla(BaseModel):
    obra_social: int
    obra_social_nombre: str = ""
    periodo: str
    total: int
    grabables: int
    duplicadas: int
    omitidas: int
    descartadas: int = 0
    por_elegir: int = 0
    con_aviso: int = 0
    equipos: int = 0
    importe_total: Decimal = Decimal("0.00")
    # Pacientes con número que no estaban en el padrón y se registran al confirmar.
    afiliados_nuevos: int = 0
    # De lo que entra: filas sin número de autorización, y de ésas las que tampoco tienen
    # fecha (no hay con qué reconocerlas como ya cargadas).
    sin_autorizacion: int = 0
    no_verificables: int = 0


class PlanillaOut(BaseModel):
    resumen: ResumenPlanilla
    filas: List[FilaPlanillaResultado]


class PeriodosPlanillaOut(PeriodosOut):
    """Períodos conocidos de la obra social y el primero en el que se puede cargar.

    Se puede importar a cualquier período desde `minimo` (el último cerrado + 1) en
    adelante, siempre que no esté cerrado; `minimo` es `None` si la obra social todavía
    no tiene ningún período cerrado."""
    minimo: Optional[str] = None


class LoteOut(BaseModel):
    lote: str
    # validando | grabando | guardando | grabada | cancelada | error | desconocido
    estado: str = "desconocido"
    hecho: int = 0
    total: int = 0
    grabadas: int = 0
    importe_total: Decimal = Decimal("0.00")
