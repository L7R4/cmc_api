from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

#region NOTICIAS 
# Lo que recibe el POST (crear)
class NoticiaCreateIn(BaseModel):
    titulo: str
    contenido: str
    resumen: str
    publicada: Optional[bool] = True
    autor: Optional[str] = None

class NoticiaUpdateIn(BaseModel):
    titulo: Optional[str] = None
    contenido: Optional[str] = None
    resumen: Optional[str] = None
    publicada: Optional[bool] = None
    autor: Optional[str] = None
    portada: Optional[str] = None


class NoticiaOut(BaseModel):
    id: str
    titulo: str
    contenido: str
    resumen: str
    autor: str
    publicada: bool
    portada: Optional[str] = None
    fechaCreacion: datetime = Field(..., alias="fecha_creacion")
    fechaActualizacion: datetime = Field(..., alias="fecha_actualizacion")

    class Config:
        populate_by_name = True
#endregion


#region DOCUMENTO NOTICIAS 
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
#endregion


class Config:
    populate_by_name = True