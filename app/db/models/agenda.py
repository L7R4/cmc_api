"""Los calendarios del Colegio: feriados, cumpleaños y tareas del mes.

Una tabla y no tres: son tres cosas para el usuario pero la misma para la base
—algo que pasa en una fecha y tiene un título— y la vista más usada es la que los
muestra juntos en un mes.

Lo que cambia entre ellos es cómo se ubican en el almanaque, y eso es
`recurrencia`, no `tipo`:

    'unica'    usa `fecha`        un día puntual de un año puntual
    'anual'    usa `dia` + `mes`  se repite cada año   (cumpleaños)
    'mensual'  usa `dia`          se repite cada mes   (tareas)

Un cumpleaños como `fecha` completa obligaría a una fila por año. Carnaval y
Semana Santa se mueven, así que van como 'unica'.
"""
import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, Index, Integer, SmallInteger, String, Boolean, Date
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class AgendaEvento(Base):
    """Una entrada de cualquiera de los tres calendarios."""

    __tablename__ = "agenda_eventos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: Qué calendario. Es lo único que separa las tres vistas.
    tipo: Mapped[str] = mapped_column(
        Enum("feriado", "cumpleanos", "tarea", name="agenda_tipo_enum"), nullable=False
    )
    titulo: Mapped[str] = mapped_column(String(200), nullable=False)
    descripcion: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    #: Cómo se ubica en el almanaque. Ver el docstring del módulo.
    recurrencia: Mapped[str] = mapped_column(
        Enum("unica", "anual", "mensual", name="agenda_recurrencia_enum"),
        nullable=False,
        server_default="unica",
    )
    #: Sólo para `recurrencia='unica'`.
    fecha: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)
    #: 1..31. Para `anual` y `mensual`. Un 31 en un mes de 30 se resuelve al
    #: expandir (se corre al último día), no acá: la fila guarda lo que el
    #: usuario dijo.
    dia: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    #: 1..12. Sólo para `anual`.
    mes: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)

    #: `ListadoMedico.ID` cuando el cumpleaños es el de un socio. Sin FK: la
    #: entrada del calendario no tiene por qué desaparecer con el legajo, y
    #: además se usan cumpleaños de empleados que no son socios.
    medico_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    #: Área o persona a cargo. Es lo que convierte una tarea en accionable —
    #: "cerrar el período" sin responsable no lo hace nadie.
    responsable: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    #: Color del punto en el calendario. `null` = el del tipo.
    color: Mapped[Optional[str]] = mapped_column(String(9), nullable=True)

    #: Baja lógica. Un feriado que dejó de serlo no se borra: sigue explicando
    #: por qué el Colegio estuvo cerrado el año pasado.
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="1")

    creado_en: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime, nullable=True, server_default=func.now()
    )
    actualizado_en: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime, nullable=True, server_default=func.now(), onupdate=func.now()
    )
    actualizado_por: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        # Las dos consultas reales: "el calendario de feriados" y "qué cae en
        # este mes". La segunda no puede indexarse del todo porque las anuales
        # se filtran por `mes` y las únicas por `fecha`, así que se indexan las
        # dos columnas por separado.
        Index("ix_agenda_tipo_activo", "tipo", "activo"),
        Index("ix_agenda_fecha", "fecha"),
        Index("ix_agenda_mes_dia", "mes", "dia"),
    )
