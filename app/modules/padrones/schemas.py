from typing import Optional
from pydantic import BaseModel, Field


class ObraSocialOut(BaseModel):
    NRO_OBRA_SOCIAL: int
    NOMBRE: str
    CODIGO: Optional[str] = None


class PadronOut(BaseModel):
    ID: int
    NRO_SOCIO: int
    NRO_OBRASOCIAL: int
    CATEGORIA: Optional[str] = None
    ESPECIALIDAD: Optional[str] = None
    TELEFONO_CONSULTA: Optional[str] = None
    MATRICULA_PROV: Optional[int] = None
    MATRICULA_NAC: Optional[int] = None
    NOMBRE: Optional[str] = None
    MARCA: Optional[str] = None

    model_config = {"from_attributes": True}


class PadronUpdate(BaseModel):
    CATEGORIA: Optional[str] = Field(None, min_length=1, max_length=1)
    ESPECIALIDAD: Optional[str] = None
    TELEFONO_CONSULTA: Optional[str] = None
    MATRICULA_PROV: Optional[int] = None
    MATRICULA_NAC: Optional[int] = None
    NOMBRE: Optional[str] = None
    MARCA: Optional[str] = None


class MedicoOSItemOut(BaseModel):
    ID: int
    NRO_SOCIO: int
    NOMBRE: str | None = None
    MATRICULA_PROV: int | None = None
    MATRICULA_NAC: int | None = None
    CATEGORIA: str | None = None
    TELEFONO_CONSULTA: str | None = None
    MARCA: str | None = None
    OBRA_SOCIAL: str | None = None
    ESPECIALIDADES: list[str] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class PageMedicoOS(BaseModel):
    items: list[MedicoOSItemOut]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    size: int = Field(ge=1, le=200)


class AsignacionesOut(BaseModel):
    conceps: list[int] = Field(default_factory=list)
    espec: list[int] = Field(default_factory=list)
