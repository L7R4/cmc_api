"""
Tokens de dispositivo para las notificaciones push de la app móvil (cmc-app).

Cada fila es UN dispositivo de UN socio: el app registra su Expo push token al
iniciar sesión (POST /api/mobile/dispositivos) y lo da de baja al cerrarla. Un
socio puede tener varios (celular + tablet), y un mismo teléfono puede cambiar
de dueño, por eso la unicidad es por token y no por médico.

Lo consume el despacho de avisos (app/modules/avisos/push.py). Columnas en
lower_case: es tabla nueva, no legacy.
"""
import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base

# Plataforma informada por el app. Sin Enum a propósito (mismo criterio que
# TIPOS_AVISO): sumar una no debe requerir migración.
PLATAFORMAS: tuple[str, ...] = ("ios", "android", "web")

# Los tokens de Expo son "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]" (~41 chars)
# o, con FCM/APNs crudo, bastante más largos. 255 cubre ambos con margen.
TOKEN_MAX = 255


class DispositivoPush(Base):
    __tablename__ = "dispositivos_push"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Dueño actual del dispositivo (ListadoMedico.ID). CASCADE: si se borra el
    # médico, sus tokens no tienen sentido y no debe quedar a quién mandarle.
    medico_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("listado_medico.ID", ondelete="CASCADE"),
        nullable=False,
    )
    # UNIQUE: si el mismo teléfono lo usa otro socio, el token se reasigna en vez
    # de duplicarse — así el dueño anterior deja de recibir sus avisos.
    expo_push_token: Mapped[str] = mapped_column(
        String(TOKEN_MAX), nullable=False, unique=True
    )
    plataforma: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # false = logout, o Expo respondió DeviceNotRegistered. Se conserva la fila
    # para poder reactivarla si el mismo token vuelve a registrarse.
    activo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    # Último registro/refresh del token. Permite purgar dispositivos muertos.
    last_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # Cubre el despacho (WHERE activo=1) y la baja por socio en el logout.
        Index("ix_dispositivos_push_activo_medico", "activo", "medico_id"),
    )
