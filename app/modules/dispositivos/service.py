"""Acceso a datos de dispositivos_push. Las funciones corren dentro de la
transacción del caller (igual que modules/avisos/service.py): no hacen commit.

Sólo lo usa el BFF móvil (registro/baja del token) y el despacho de avisos
(app/modules/avisos/push.py, que lee los tokens activos).
"""
from __future__ import annotations

import re
from typing import Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.db.models.dispositivos_push import PLATAFORMAS, DispositivoPush

# Formato que emite expo-notifications. Validarlo evita llenar la tabla de basura
# y que el despacho gaste requests en tokens que Expo va a rechazar igual.
TOKEN_RE = re.compile(r"^Expo(nent)?PushToken\[[A-Za-z0-9_\-]+\]$")


def token_valido(token: str) -> bool:
    return bool(TOKEN_RE.match(token.strip()))


def normalizar_plataforma(plataforma: Optional[str]) -> Optional[str]:
    if not plataforma:
        return None
    p = plataforma.strip().lower()
    return p if p in PLATAFORMAS else None


async def registrar(
    db: AsyncSession,
    *,
    medico_id: int,
    token: str,
    plataforma: Optional[str] = None,
) -> DispositivoPush:
    """Alta o refresh del token para este médico.

    Si el token ya existe se REASIGNA al médico actual en vez de duplicarse: es
    el caso del teléfono que cambia de dueño, o del socio que vuelve a entrar
    después de un logout. Así el dueño anterior deja de recibir sus avisos.
    """
    token = token.strip()
    existente = (
        await db.execute(
            select(DispositivoPush).where(DispositivoPush.expo_push_token == token)
        )
    ).scalars().first()

    if existente:
        existente.medico_id = medico_id
        existente.plataforma = normalizar_plataforma(plataforma) or existente.plataforma
        existente.activo = True
        # now() de SQL, para no depender del reloj del proceso de la app.
        existente.last_seen_at = func.now()
        return existente

    nuevo = DispositivoPush(
        medico_id=medico_id,
        expo_push_token=token,
        plataforma=normalizar_plataforma(plataforma),
        activo=True,
    )
    db.add(nuevo)
    return nuevo


async def baja(db: AsyncSession, *, medico_id: int, token: str) -> int:
    """Desactiva el token en el logout. Acotado al médico del token de sesión:
    si no es suyo no hace nada, así nadie puede apagarle las notificaciones a
    otro socio mandando un token ajeno. Devuelve las filas afectadas."""
    result = await db.execute(
        update(DispositivoPush)
        .where(
            DispositivoPush.expo_push_token == token.strip(),
            DispositivoPush.medico_id == medico_id,
        )
        .values(activo=False)
    )
    return int(result.rowcount or 0)


async def desactivar_tokens(db: AsyncSession, tokens: Sequence[str]) -> int:
    """Baja masiva de los tokens que Expo reportó como muertos
    (DeviceNotRegistered). Sin filtro por médico: los manda el proveedor."""
    if not tokens:
        return 0
    result = await db.execute(
        update(DispositivoPush)
        .where(DispositivoPush.expo_push_token.in_(list(tokens)))
        .values(activo=False)
    )
    return int(result.rowcount or 0)


async def tokens_activos(db: AsyncSession) -> list[str]:
    """Destinatarios de un aviso: todos los dispositivos activos.

    Trae sólo la columna del token (no la entidad) — con miles de socios la
    diferencia importa. Usa el índice (activo, medico_id).
    """
    rows = await db.execute(
        select(DispositivoPush.expo_push_token).where(DispositivoPush.activo == True)  # noqa: E712
    )
    return [t for (t,) in rows.all()]
