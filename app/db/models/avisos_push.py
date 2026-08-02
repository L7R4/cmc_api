"""
Avisos para los socios de la app móvil (cmc-app).

OJO CON EL NOMBRE: la tabla se llama `avisos_push`, NO `avisos`. `avisos` ya
existe en el esquema legacy (models/legacy.py → class Avisos: ID/ARCHIVO/FECHA/
EXISTE/AVISO, la usa el PHP viejo) y no tiene nada que ver con esto.

Los da de alta el panel web (app/modules/avisos, scope `avisos:gestionar`) y los
lee la app móvil (GET /api/mobile/avisos, sólo los activos). Columnas en
lower_case: es tabla nueva, no legacy.
"""
import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base

# Tipo del aviso: define el ícono/color con el que el app lo muestra. Misma
# lista que el selector del panel (TIPOS_AVISO en avisos.types.ts del front).
# Sin Enum a propósito: sumar un tipo no debe requerir migración — la validación
# vive en los schemas.
TIPOS_AVISO: tuple[str, ...] = (
    "General",
    "Institucional",
    "Novedades",
    "Beneficios",
    "Urgente",
)

# Estado del despacho de la notificación push:
#   pendiente → guardado y visible en el app, pero el push todavía no salió
#   enviado   → el proveedor (FCM/Expo) aceptó el envío
#   error     → el proveedor lo rechazó, el motivo queda en push_error
# Hoy TODOS quedan en 'pendiente': no hay integración de push todavía (no existe
# registro de tokens de dispositivo ni credenciales del proveedor). El socio ve
# el aviso al abrir el app; lo que falta es que el teléfono se despierte.
ESTADOS_PUSH: tuple[str, ...] = ("pendiente", "enviado", "error")


class AvisoPush(Base):
    __tablename__ = "avisos_push"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 120 da margen sobre el límite del panel (80): los proveedores de push
    # truncan el título cerca de los 65 caracteres, así que el tope real es de UX.
    titulo: Mapped[str] = mapped_column(String(120), nullable=False)
    mensaje: Mapped[str] = mapped_column(String(500), nullable=False)
    tipo: Mapped[str] = mapped_column(
        String(40), nullable=False, default="General", server_default="General"
    )
    # Cuándo pasó a estar visible para el app. Es el orden del listado móvil.
    publicado_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    # Permite bajar un aviso del app sin borrar el registro (queda el historial).
    activo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    push_estado: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pendiente", server_default="pendiente"
    )
    push_error: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Dispositivos alcanzados. NULL mientras no haya despacho real de push.
    destinatarios: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Admin que lo publicó (ListadoMedico.ID). SET NULL para no perder el aviso
    # si se borra el usuario — mismo criterio que solicitud_cambio_medico.
    enviado_por: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("listado_medico.ID", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # Cubre el listado del app (WHERE activo=1 ORDER BY publicado_at DESC).
        Index("ix_avisos_push_activo_publicado", "activo", "publicado_at"),
    )
