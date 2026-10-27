from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, field_serializer


class _ConImportes(BaseModel):
    @field_serializer("*", when_used="json")
    def _decimal_a_float(self, v):
        return float(v) if isinstance(v, Decimal) else v


class RetencionGananciasFila(_ConImportes):
    medico_id: int
    nro_socio: Optional[int] = None
    nombre: str
    cuit: Optional[str] = None
    tipo_sujeto: str
    honorarios: Decimal
    gastos: Decimal
    base: Decimal
    calculado: Decimal
    retenido: Decimal


class RetencionGananciasTotales(_ConImportes):
    honorarios: Decimal
    gastos: Decimal
    base: Decimal
    calculado: Decimal
    retenido: Decimal


class AgenteRetencion(BaseModel):
    razon_social: Optional[str] = None
    cuit: Optional[str] = None
    domicilio: Optional[str] = None
    localidad: Optional[str] = None
    nro_agente_retencion: Optional[str] = None


class RetencionGananciasListado(BaseModel):
    anio: int
    mes: int
    pagos_incluidos: List[int]
    # Pagos del mes todavía abiertos: no entran (pueden cambiar) y la pantalla lo avisa.
    pagos_abiertos: List[int]
    agente: AgenteRetencion
    filas: List[RetencionGananciasFila]
    totales: RetencionGananciasTotales


class TramoRead(_ConImportes):
    desde: Decimal
    hasta: Optional[Decimal] = None
    importe_fijo: Decimal
    porcentaje: Decimal


class EscalaRead(_ConImportes):
    id: int
    tipo_sujeto: str
    vigente_desde: str
    minimo_no_imponible: Decimal
    retencion_minima: Decimal
    observacion: Optional[str] = None
    tramos: List[TramoRead]
