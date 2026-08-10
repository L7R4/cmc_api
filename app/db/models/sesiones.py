"""Sesiones: refresh tokens con estado en la base (A5 de la auditoría).

Un JWT es válido porque su firma verifica, no porque el servidor lo recuerde. Eso
es lo que lo hace barato —cero consultas por request— y también lo que hace que
**no se pueda revocar**: hasta que llega su `exp`, cualquiera que lo tenga entra.
Con `REFRESH_DAYS = 15` eso son dos semanas de acceso continuo a partir de un
token robado, y el `/auth/logout` anterior solo borraba la cookie del navegador
del usuario legítimo — al atacante, que ya tiene una copia, no lo tocaba.

Esta tabla le pone estado al refresh: un refresh solo sirve si su `jti` está acá
y no está revocado. Vale por sesión, no por token: al rotar, la fila vieja queda
revocada apuntando (`reemplazado_por`) a la nueva, y eso permite lo importante,
que es la **detección de reuso**.

## Detección de reuso

Si llega un `jti` que ya está revocado, hay dos explicaciones y ninguna es
buena: o el atacante está usando un refresh que el usuario legítimo ya rotó, o
el usuario está usando uno que el atacante rotó. No se puede distinguir cuál,
así que se revoca **toda la cadena** del usuario y los dos vuelven al login. El
legítimo sabe su contraseña; el atacante, no.

El access token no se toca acá: vive `ACCESS_MINUTES` (15) y se corta con
`ListadoMedico.tokens_valid_from`. Ver `app/auth/sessions.py`.
"""
import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        # El lookup de cada /refresh es por jti; el de revocación masiva, por
        # usuario.
        Index("ix_refresh_tokens_user", "user_id"),
        Index("ix_refresh_tokens_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # El `jti` del JWT. Único: es la identidad de la sesión.
    jti: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listado_medico.ID", ondelete="CASCADE"), nullable=False
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    # Para leer el incidente después: "logout", "rotacion", "cambio_password",
    # "cambio_permisos", "reuso_detectado".
    revoked_reason: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # jti de la sesión que reemplazó a esta en la rotación. Es lo que permite
    # reconstruir la cadena cuando se detecta un reuso.
    replaced_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Contexto del alta, para que el usuario pueda reconocer sus sesiones y para
    # investigar un robo. `web` o `mobile`.
    origen: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)

    @property
    def activo(self) -> bool:
        return self.revoked_at is None
