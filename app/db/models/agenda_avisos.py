"""
Avisos de la agenda: qué evento de hoy le toca ver a cada administrador.

Una fila = un evento de un día para un administrador. La genera el cron diario
(`POST /api/agenda/avisos/generar-hoy`) y el panel la lee y la marca como leída.

La unicidad (usuario, evento, fecha) hace idempotente al cron: correrlo dos
veces el mismo día no duplica avisos. Tabla nueva, columnas en lower_case.
"""
import datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class AgendaAviso(Base):
    __tablename__ = "agenda_avisos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Destinatario (ListadoMedico.ID). CASCADE: sin usuario no hay a quién avisar.
    medico_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listado_medico.ID", ondelete="CASCADE"), nullable=False
    )
    evento_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agenda_eventos.id", ondelete="CASCADE"), nullable=False
    )
    # El día en que el evento ocurre (y se avisa).
    fecha: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    leido: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("medico_id", "evento_id", "fecha", name="uq_agenda_avisos"),
        # La lectura real: "los no leídos de este usuario hoy".
        Index("ix_agenda_avisos_medico_fecha", "medico_id", "fecha", "leido"),
    )
