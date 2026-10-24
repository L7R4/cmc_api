from sqlalchemy import JSON, BigInteger, Column, DateTime, Index, Integer, SmallInteger, String, Text
from sqlalchemy.sql import func

from app.db.base import Base


class AuditLog(Base):
    """Auditoría general: una fila por petición que modifica algo. Ver
    docs/api/auditoria.md para el diseño completo. Hasta el 2026-10-22 la tabla se
    llamaba `audit_log`."""

    __tablename__ = "audit_log_general"

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
        Index("ix_audit_log_general_user_timestamp", "user_id", "timestamp"),
        Index("ix_audit_log_general_route_timestamp", "route", "timestamp"),
    )


class AuditLogFacturacion(Base):
    """Historial de cada prestación (`detalle_facturacion`): una fila por cambio y por
    prestación, escrita en la misma transacción que el cambio. Nunca se edita ni se
    borra, y no se purga. Ver `app/modules/facturacion/historial.py`."""

    __tablename__ = "audit_log_facturacion"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Sin FK, como la auditoría general: el historial sobrevive a cualquier borrado.
    prestacion_id = Column(BigInteger, nullable=False)
    ocurrido_en = Column(DateTime, server_default=func.now(), nullable=False)
    tipo = Column(String(30), nullable=False)
    # Sólo cuando el evento cambia el estado de la fila (A, C, L o X).
    estado_antes = Column(String(2), nullable=True)
    estado_despues = Column(String(2), nullable=True)
    # colegio · medico · sistema · os · importacion
    actor_tipo = Column(String(12), nullable=False)
    # NRO_SOCIO de quien lo hizo; el nombre se resuelve al leer.
    actor_socio = Column(Integer, nullable=True)
    motivo_codigo = Column(String(30), nullable=True)
    motivo_texto = Column(String(255), nullable=True)
    # Sólo los campos que cambiaron: {"campo": [antes, después]}.
    cambios = Column(JSON, nullable=True)
    # Dónde estaba la prestación: obra social, período, versión, equipo, factura…
    contexto = Column(JSON, nullable=True)
    # El mismo de `audit_log_general.request_id`: lleva a la petición original.
    request_id = Column(String(32), nullable=True)
    # en_vivo · reconstruido
    fuente = Column(String(12), nullable=False, server_default="en_vivo")

    __table_args__ = (
        Index("ix_audit_log_facturacion_prestacion", "prestacion_id", "ocurrido_en"),
        Index("ix_audit_log_facturacion_tipo", "tipo", "ocurrido_en"),
        Index("ix_audit_log_facturacion_actor", "actor_socio", "ocurrido_en"),
        Index("ix_audit_log_facturacion_request", "request_id"),
    )
