from typing import Optional, Literal, List
from datetime import datetime, date
from pydantic import BaseModel

ApplicationStatus = Literal["nueva", "pendiente", "aprobada", "rechazada"]

class SolicitudListItem(BaseModel):
    id: int
    medico_id: int
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    status: str  # "nueva" | "pendiente" | "aprobada" | "rechazada"
    submitted_date: datetime
    member_type: Optional[str] = None
    join_date: Optional[datetime] = None
    observations: Optional[str] = None
    rejection_reason: Optional[str] = None

class SolicitudDetailOut(SolicitudListItem):
    documento: Optional[str] = None
    provincia: Optional[str] = None
    localidad: Optional[str] = None
    categoria: Optional[str] = None


class ApproveIn(BaseModel):
    observaciones: Optional[str] = None
    nro_socio: Optional[int] = None

class RejectIn(BaseModel):
    observaciones: Optional[str] = None
