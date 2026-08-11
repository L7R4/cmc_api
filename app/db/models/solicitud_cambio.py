"""
Solicitudes de corrección de datos enviadas por los médicos desde la app móvil
("Reclamar cambios"): el médico avisa que un dato suyo del padrón/perfil está mal.

Las crea la app (POST /api/mobile/solicitudes-cambio) y las resuelve la web
(app/modules/solicitudes_cambio). Aprobar NO edita el ListadoMedico: sólo marca
la solicitud; el admin aplica el cambio a mano con el valor_propuesto a la vista.

Es un flujo distinto al de SolicitudRegistro (alta de socio), por eso tabla y
módulo propios.
"""
import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base

ESTADOS_SOLICITUD_CAMBIO: tuple[str, ...] = ("pendiente", "aprobada", "rechazada")

# Campos que la app ofrece en el selector. La columna es String, no Enum: se
# aceptan otros valores ("..." en el contrato) sin migrar. Esto es sólo el
# vocabulario conocido para etiquetas/filtros.
CAMPOS_CONOCIDOS: tuple[str, ...] = (
    "telefono",
    "email",
    "domicilio",
    "padron",
    "especialidad",
    "general",
)


class SolicitudCambioMedico(Base):
    __tablename__ = "solicitud_cambio_medico"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Credencial pública del médico — se guarda siempre, aunque el médico se borre.
    nro_socio: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # PK interna. SET NULL al borrar el médico: la solicitud queda como histórico.
    medico_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("listado_medico.ID", ondelete="SET NULL"), nullable=True, index=True
    )
    campo: Mapped[str] = mapped_column(String(40), nullable=False)
    valor_actual: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    valor_propuesto: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Solicitud de FORMULARIO COMPLETO: el médico revisa todo su legajo y manda
    # de una todos los campos que quiere corregir.
    #
    #   {"tele_particular": {"actual": "3794...", "propuesto": "3795..."}, ...}
    #
    # Convive con `campo`/`valor_propuesto`, que siguen siendo el contrato de la
    # app móvil (un campo por solicitud). Cuando `cambios` viene poblado, esas
    # tres columnas guardan el PRIMER cambio, para que la bandeja y los listados
    # viejos sigan mostrando algo con sentido sin tocarlos.
    cambios: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    mensaje: Mapped[str] = mapped_column(Text, nullable=False)
    estado: Mapped[str] = mapped_column(
        Enum(*ESTADOS_SOLICITUD_CAMBIO, name="estado_solicitud_cambio"),
        nullable=False,
        server_default="pendiente",
    )
    # ListadoMedico.ID del admin que resolvió. Sin FK: es un dato de auditoría y
    # no queremos que borrar un admin bloquee/altere la solicitud.
    revisado_por: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    revisado_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    # Cuándo se ESCRIBIERON los cambios en listado_medico. Distinto de
    # `revisado_at`: una solicitud puede aprobarse y, si ningún campo era
    # aplicable, no modificar nada. NULL = no se aplicó nada.
    aplicado_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime, nullable=True
    )
    respuesta_admin: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # Bandeja del admin: WHERE estado=? ORDER BY created_at DESC.
        Index("ix_solicitud_cambio_estado_created", "estado", "created_at"),
    )
