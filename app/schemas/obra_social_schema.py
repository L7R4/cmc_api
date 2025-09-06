from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ObraSocialBase(BaseModel):
    NRO_OBRASOCIAL: int = Field(..., description="Número identificador de la obra social", example=0)
    OBRA_SOCIAL: str = Field(..., description="Nombre de la obra social", example="OSDE")
    MARCA: str = Field(..., description="Marca (1 char)", example="N")
    VER_VALOR: str = Field(..., description="Indicador ver valor (1 char)", example="N")



class ObraSocialCreate(ObraSocialBase):
    pass


class ObraSocialUpdate(BaseModel):
    NRO_OBRASOCIAL: Optional[int] = None
    OBRA_SOCIAL: Optional[str] = None
    MARCA: Optional[str] = None
    VER_VALOR: Optional[str] = None

class ObraSocialOut(ObraSocialBase):
    ID: int = Field(..., description="PK en la tabla obras_sociales")
    model_config = {"from_attributes": True}