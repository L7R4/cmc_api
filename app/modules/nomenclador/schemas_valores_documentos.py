import datetime
from typing import Optional

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
