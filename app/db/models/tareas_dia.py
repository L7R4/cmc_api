"""
Cuaderno de tareas del Inicio del panel.

Cada fila es UNA tarea de UN usuario. Es personal: ningún endpoint devuelve ni
toca tareas de otro médico/usuario, el dueño sale siempre del token.

Tabla nueva, columnas en lower_case. Sin Enum a propósito (mismo criterio que
TIPOS_AVISO): sumar una prioridad no debe requerir migración.
"""
import datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base

# De la más urgente a la menos: es el orden de la lista en el front.
PRIORIDADES: tuple[str, ...] = ("alta", "media", "baja")

# Mismos topes que el front (useTareasDelDia.ts).
TEXTO_MAX = 140
TAREAS_MAX = 30


class TareaDia(Base):
    __tablename__ = "tareas_dia"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Dueño (ListadoMedico.ID, el claim `uid` del token). CASCADE: si se borra
    # el usuario, su cuaderno no tiene a quién pertenecer.
    medico_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("listado_medico.ID", ondelete="CASCADE"),
        nullable=False,
    )
    texto: Mapped[str] = mapped_column(String(TEXTO_MAX), nullable=False)
    hecha: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    prioridad: Mapped[str] = mapped_column(
        String(10), nullable=False, default="media", server_default="media"
    )
    # Día en que se tildó. Al empezar un día nuevo las tildadas se van; lo
    # pendiente se arrastra. NULL mientras no esté hecha.
    hecha_el: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # Toda consulta es "las tareas de este usuario".
        Index("ix_tareas_dia_medico", "medico_id"),
    )
