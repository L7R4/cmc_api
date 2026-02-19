from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, Text
from sqlalchemy.sql import func

from app.db.base import Base


class SolicitudRegistro(Base):
    __tablename__ = "solicitudes_registros"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    estado        = Column(Enum("pendiente","aprobada","rechazada", name="estado_solicitud"),
                           nullable=False, server_default="pendiente", index=True)

    medico_id     = Column(Integer, ForeignKey("listado_medico.ID", ondelete="CASCADE"), nullable=False, index=True)
    aprobado_por  = Column(Integer, nullable=True)
    aprobado_at   = Column(DateTime(timezone=True), nullable=True)
    observaciones = Column(Text, nullable=True)

    created_at    = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at    = Column(DateTime(timezone=True), onupdate=func.now())
