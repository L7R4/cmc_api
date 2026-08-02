"""Schemas de las solicitudes de cambio de datos (bandeja del admin).

El body que manda la app móvil vive en modules/mobile/schemas.py
(SolicitudCambioIn): son contratos separados.
"""
import datetime
from typing import Dict, Optional

from pydantic import BaseModel, Field, field_validator


class SolicitudCambioOut(BaseModel):
    id: int
    nro_socio: int
    medico_id: Optional[int] = None
    # Nombre del médico resuelto desde listado_medico (no se persiste acá).
    medico_nombre: Optional[str] = None
    campo: str
    valor_actual: Optional[str] = None
    valor_propuesto: Optional[str] = None
    mensaje: str
    estado: str
    revisado_por: Optional[int] = None
    revisado_por_nombre: Optional[str] = None
    revisado_at: Optional[datetime.datetime] = None
    respuesta_admin: Optional[str] = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class SolicitudCambioCounts(BaseModel):
    """Contadores para los badges de la bandeja."""
    total: int
    pendiente: int
    aprobada: int
    rechazada: int


class SolicitudCambioListOut(BaseModel):
    items: list[SolicitudCambioOut]
    total: int
    counts: SolicitudCambioCounts


class ResolverIn(BaseModel):
    """Body de approve/reject. En rechazo el motivo es obligatorio (lo pide la UI)."""
    respuesta_admin: Optional[str] = Field(None, max_length=2000)

    @field_validator("respuesta_admin", mode="after")
    @classmethod
    def _strip(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return v.strip() or None


# Etiquetas legibles por campo — las usa el front para los chips de la bandeja.
CAMPO_LABELS: Dict[str, str] = {
    "telefono": "Teléfono",
    "email": "Email",
    "domicilio": "Domicilio",
    "padron": "Padrón",
    "especialidad": "Especialidad",
    "general": "General",
}
