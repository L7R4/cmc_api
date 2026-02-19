from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

TipoPublicacion = Literal["Blog", "Noticia", "Curso"]


class NoticiaCreateIn(BaseModel):
    titulo: str
    contenido: str
    resumen: str
    publicada: Optional[bool] = True
    tipo: TipoPublicacion = "Noticia"
    autor: Optional[str] = None


class NoticiaUpdateIn(BaseModel):
    titulo: Optional[str] = None
    contenido: Optional[str] = None
    resumen: Optional[str] = None
    publicada: Optional[bool] = None
    tipo: Optional[TipoPublicacion] = None
    autor: Optional[str] = None
    portada: Optional[str] = None


class NoticiaOut(BaseModel):
    id: str
    titulo: str
    contenido: str
    resumen: str
    autor: str
    publicada: bool
    tipo: Optional[str] = None
    portada: Optional[str] = None
    fechaCreacion: datetime = Field(..., alias="fecha_creacion")
    fechaActualizacion: datetime = Field(..., alias="fecha_actualizacion")

    class Config:
        populate_by_name = True


class DocumentoNoticiasOut(BaseModel):
    id: int
    label: Optional[str] = None
    original_name: str
    filename: str
    content_type: Optional[str] = None
    size: Optional[int] = None
    path: str


class NoticiaDetailOut(NoticiaOut):
    documentos: List[DocumentoNoticiasOut] = []


class PublicidadMedicoOut(BaseModel):
    id: int
    medico_id: int
    medico_nombre: str | None = None
    activo: bool
    adjunto_filename: str | None = None
    adjunto_content_type: str | None = None
    adjunto_size: int | None = None
    adjunto_path: str | None = None
    createdAt: datetime = Field(..., alias="created_at")
    updatedAt: datetime = Field(..., alias="updated_at")

    class Config:
        populate_by_name = True
