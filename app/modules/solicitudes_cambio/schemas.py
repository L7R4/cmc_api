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
    # Diff completo cuando la solicitud vino del formulario del portal.
    cambios: Optional[Dict[str, dict]] = None
    aplicado_at: Optional[datetime.datetime] = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class SolicitudCambioCrearIn(BaseModel):
    """Body del reclamo abierto por el propio socio desde el portal web.

    nro_socio y medico_id NO viajan acá: salen del token, para que nadie pueda
    abrir un reclamo a nombre de otro. Mismo criterio (y mismos límites) que
    SolicitudCambioIn del BFF móvil — son dos contratos separados a propósito,
    porque los clientes evolucionan por separado.
    """
    campo: str = Field(..., min_length=2, max_length=40)
    valor_actual: Optional[str] = Field(None, max_length=255)
    valor_propuesto: Optional[str] = Field(None, max_length=255)
    mensaje: str = Field(..., min_length=1, max_length=2000)

    @field_validator("campo", "mensaje", mode="after")
    @classmethod
    def _strip_required(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("No puede estar vacío")
        return s

    @field_validator("valor_actual", "valor_propuesto", mode="after")
    @classmethod
    def _strip_optional(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return v.strip() or None


class SolicitudCambioFormularioIn(BaseModel):
    """Formulario completo: el médico manda todos sus datos editables.

    `valores` es {campo: valor}. NO viaja `valor_actual`: el backend lo lee de
    la base para que la comparación sea contra el legajo real y no contra lo que
    el cliente diga que había.
    """
    valores: Dict[str, Optional[str]] = Field(default_factory=dict)
    mensaje: str = Field("", max_length=2000)

    @field_validator("mensaje", mode="after")
    @classmethod
    def _strip(cls, v: str) -> str:
        return (v or "").strip()


class CampoEditableOut(BaseModel):
    """Un campo que el médico puede pedir corregir, con lo que figura hoy."""
    campo: str
    etiqueta: str
    valor_actual: Optional[str] = None


class SolicitudCambioMiaOut(BaseModel):
    """Lo que el socio ve de su propio reclamo. No expone revisado_por: quién lo
    resolvió es dato interno de la bandeja, no del reclamante."""
    id: int
    campo: str
    valor_actual: Optional[str] = None
    valor_propuesto: Optional[str] = None
    mensaje: str
    estado: str
    respuesta_admin: Optional[str] = None
    revisado_at: Optional[datetime.datetime] = None
    created_at: datetime.datetime


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
