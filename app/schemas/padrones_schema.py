from typing import Optional
from pydantic import BaseModel, Field

# Catálogo de obras sociales (para listar en la UI)
class ObraSocialOut(BaseModel):
    NRO_OBRA_SOCIAL: int
    NOMBRE: str
    CODIGO: Optional[str] = None  # opcional; si no existe en tu tabla se completa como "OS{n:03d}"

# Asociación médico ↔ obra social
class PadronOut(BaseModel):
    ID: int
    NRO_SOCIO: int
    NRO_OBRASOCIAL: int  # nombre según tu modelo sqlalchemy
    CATEGORIA: Optional[str] = None
    ESPECIALIDAD: Optional[str] = None
    TELEFONO_CONSULTA: Optional[str] = None
    MATRICULA_PROV: Optional[int] = None
    MATRICULA_NAC: Optional[int] = None
    NOMBRE: Optional[str] = None
    MARCA: Optional[str] = None

    model_config = {"from_attributes": True}

# Campos opcionales para cuando quieras actualizar algo del vínculo
class PadronUpdate(BaseModel):
    CATEGORIA: Optional[str] = Field(None, min_length=1, max_length=1)
    ESPECIALIDAD: Optional[str] = None
    TELEFONO_CONSULTA: Optional[str] = None
    MATRICULA_PROV: Optional[int] = None
    MATRICULA_NAC: Optional[int] = None
    NOMBRE: Optional[str] = None
    MARCA: Optional[str] = None
