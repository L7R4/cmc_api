import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class ValorDocumentoOut(BaseModel):
    """Un documento respaldatorio de una vigencia de valores."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    obra_social_nro: int
    #: La vigencia que respalda: la misma fecha que agrupa las filas de
    #: `nm_valores` de esa actualización.
    vigencia_desde: datetime.date
    nombre_original: str
    content_type: str
    size: int
    descripcion: Optional[str] = None
    #: Ruta `/api/archivos/…`: pide token. Se abre con `abrirAdjunto()`.
    url: str
    created_at: datetime.datetime


class ObraSocialActualizada(BaseModel):
    """Una obra social que actualizó valores en una vigencia."""

    obra_social_nro: int
    nombre: str
    vigencia_desde: datetime.date
    #: Cuántas filas de `nm_valores` entraron con esa vigencia.
    codigos: int
    #: Si esa vigencia tiene documento respaldatorio cargado.
    tiene_documento: bool = False


class MesActualizaciones(BaseModel):
    """Un mes con al menos una actualización. Los meses sin nada no aparecen."""

    #: `YYYY-MM`.
    mes: str
    obras_sociales: List[ObraSocialActualizada] = []
    #: Obras sociales distintas del mes — no la cantidad de filas de arriba, que
    #: cuenta una vez por vigencia y una OS puede tener dos en el mismo mes.
    total_obras_sociales: int = 0
    total_codigos: int = 0
