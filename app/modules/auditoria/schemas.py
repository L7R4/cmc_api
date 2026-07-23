from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AuditLogListItem(BaseModel):
    id: int
    timestamp: datetime
    method: str
    path: str
    route: Optional[str] = None
    nro_socio: Optional[int] = None
    nombre_medico: Optional[str] = None
    role: Optional[str] = None
    status_code: int
    duration_ms: int
    ip: Optional[str] = None
    error_detail: Optional[str] = None

    class Config:
        from_attributes = True


class AuditLogDetail(AuditLogListItem):
    query_params: Optional[str] = None
    user_agent: Optional[str] = None
    request_body: Optional[str] = None
    request_id: Optional[str] = None


class PurgeResult(BaseModel):
    ok: bool
    deleted: int
    months: int
