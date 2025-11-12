from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field

class PublicidadMedicoOut(BaseModel):
    id: int
    medico_id: int
    medico_nombre: str | None = None  # lo resolvemos por consulta a listado_medico
    activo: bool

    adjunto_filename: str | None = None
    adjunto_content_type: str | None = None
    adjunto_size: int | None = None
    adjunto_path: str | None = None

    createdAt: datetime = Field(..., alias="created_at")
    updatedAt: datetime = Field(..., alias="updated_at")

    class Config:
        populate_by_name = True
