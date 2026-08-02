from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, SmallInteger, String, Text
from sqlalchemy.sql import func

from app.db.base import Base


class AuditLog(Base):
    """Ver docs/api/auditoria.md para el diseño completo."""

    __tablename__ = "audit_log"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    method = Column(String(10), nullable=False)
    path = Column(String(500), nullable=False)
    route = Column(String(255), nullable=True, index=True)
    query_params = Column(String(1000), nullable=True)

    # Sin FK a listado_medico.ID a propósito: el registro de auditoría debe
    # sobrevivir aunque el médico se elimine.
    user_id = Column(Integer, nullable=True, index=True)
    nro_socio = Column(Integer, nullable=True, index=True)
    role = Column(String(50), nullable=True)

    status_code = Column(SmallInteger, nullable=False, index=True)
    duration_ms = Column(Integer, nullable=False)

    ip = Column(String(45), nullable=True)
    user_agent = Column(String(255), nullable=True)

    request_body = Column(Text, nullable=True)
    error_detail = Column(String(500), nullable=True)
    request_id = Column(String(32), nullable=True)

    __table_args__ = (
        Index("ix_audit_log_user_timestamp", "user_id", "timestamp"),
        Index("ix_audit_log_route_timestamp", "route", "timestamp"),
    )
