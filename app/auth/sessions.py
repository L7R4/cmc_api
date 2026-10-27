"""Ciclo de vida de las sesiones: emitir, rotar y revocar.

Resuelve tres hallazgos que son el mismo problema visto desde tres lados:

  * **A5** — el `jti` del refresh se generaba y no se guardaba en ningún lado, así
    que un refresh robado valía 15 días y el logout no lo tocaba.
  * **A7** — cambiar la contraseña no cerraba las sesiones abiertas: quien te
    robó la cuenta seguía adentro después de que la recuperaras.
  * **A12** — quitarle un permiso o un rol a alguien no tenía efecto hasta que
    venciera su access token, porque los scopes se congelan en el JWT al emitirlo.

Son dos mecanismos, uno para cada tipo de token, y hacen falta los dos:

| | refresh | access |
|---|---|---|
| vida | 15 días | 15 minutos |
| se corta con | fila en `refresh_tokens` | `listado_medico.tokens_valid_from` |
| costo | una consulta por `/refresh` | una consulta por request |

`tokens_valid_from` es una marca de tiempo por usuario: **todo access token con
`iat` anterior queda inválido**. Es una sola columna y no necesita lista negra,
porque los access tokens son de vida corta y no se necesita revocar uno solo —
siempre se revocan todos los del usuario a la vez.

El costo de esa columna es una consulta por request en `enforce_authz`. Es un
`SELECT` de una columna por PK sobre una tabla que ya está en el buffer pool;
la alternativa —esperar hasta 15 minutos para cortarle el acceso a alguien— no
sirve para lo único que importa acá, que es reaccionar a un incidente.
"""
from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_refresh_token
from app.db.models import ListadoMedico, RefreshToken

log = logging.getLogger(__name__)

Motivo = Literal[
    "logout", "rotacion", "cambio_password", "cambio_permisos", "reuso_detectado",
    "cuenta_inactiva",
]


def _ahora() -> datetime:
    """UTC naive: la columna es DATETIME sin timezone, como el resto de la base."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ip(request: Request | None) -> Optional[str]:
    if request is None:
        return None
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()[:45]
    return request.client.host[:45] if request.client else None


def _user_agent(request: Request | None) -> Optional[str]:
    if request is None:
        return None
    ua = request.headers.get("user-agent")
    return ua[:255] if ua else None


# ── Alta ─────────────────────────────────────────────────────────────────────

async def emitir_refresh(
    db: AsyncSession,
    medico: ListadoMedico,
    *,
    origen: Literal["web", "mobile"],
    request: Request | None = None,
) -> str:
    """Crea la sesión y devuelve el refresh token firmado.

    El `jti` queda registrado antes de que el token salga: si el insert falla, el
    cliente no se lleva un token que después no va a poder canjear.
    """
    jti = secrets.token_hex(16)
    db.add(
        RefreshToken(
            jti=jti,
            user_id=medico.ID,
            expires_at=_ahora() + timedelta(days=settings.REFRESH_DAYS),
            origen=origen,
            user_agent=_user_agent(request),
            ip=_ip(request),
        )
    )
    await db.commit()
    return create_refresh_token(sub=str(medico.NRO_SOCIO), jti=jti)


# ── Rotación ─────────────────────────────────────────────────────────────────

class SesionInvalida(Exception):
    """El refresh presentado no sirve. `codigo` va tal cual al cliente."""

    def __init__(self, codigo: str) -> None:
        super().__init__(codigo)
        self.codigo = codigo


async def rotar_refresh(
    db: AsyncSession,
    medico: ListadoMedico,
    jti_viejo: str | None,
    *,
    origen: Literal["web", "mobile"],
    request: Request | None = None,
) -> str:
    """Valida el refresh presentado y lo cambia por uno nuevo.

    Rechaza —y es lo que hace que la tabla valga la pena— cuatro casos que antes
    pasaban todos:

      1. un `jti` que nunca se emitió (token fabricado con el secreto filtrado);
      2. un `jti` de otro usuario;
      3. un `jti` vencido según la base, no solo según el `exp` del JWT;
      4. un `jti` **ya rotado**, que es la señal de robo. Ahí no alcanza con
         rechazar esta request: se cierra toda la cadena del usuario.
    """
    if not jti_viejo:
        raise SesionInvalida("refresh_sin_jti")

    fila = (
        await db.execute(select(RefreshToken).where(RefreshToken.jti == jti_viejo))
    ).scalar_one_or_none()

    if fila is None:
        raise SesionInvalida("refresh_desconocido")

    if fila.user_id != medico.ID:
        raise SesionInvalida("refresh_de_otro_usuario")

    if fila.revoked_at is not None:
        # Reuso. No se puede saber si el que llegó tarde es el atacante o el
        # dueño, así que se cierran las dos: el dueño sabe su contraseña.
        log.warning(
            "reuso de refresh token: user_id=%s jti=%s (revocado el %s por '%s')",
            fila.user_id, fila.jti, fila.revoked_at, fila.revoked_reason,
        )
        await revocar_sesiones(db, fila.user_id, motivo="reuso_detectado")
        raise SesionInvalida("refresh_reusado")

    if fila.expires_at <= _ahora():
        raise SesionInvalida("refresh_expirado")

    nuevo = secrets.token_hex(16)
    fila.revoked_at = _ahora()
    fila.revoked_reason = "rotacion"
    fila.replaced_by = nuevo
    db.add(
        RefreshToken(
            jti=nuevo,
            user_id=medico.ID,
            expires_at=_ahora() + timedelta(days=settings.REFRESH_DAYS),
            origen=origen,
            user_agent=_user_agent(request),
            ip=_ip(request),
        )
    )
    await db.commit()
    return create_refresh_token(sub=str(medico.NRO_SOCIO), jti=nuevo)


# ── Baja ─────────────────────────────────────────────────────────────────────

async def revocar_por_jti(db: AsyncSession, jti: str | None, *, motivo: Motivo) -> None:
    """Cierra una sesión puntual. Es lo que hace el logout."""
    if not jti:
        return
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.jti == jti, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_ahora(), revoked_reason=motivo)
    )
    await db.commit()


async def revocar_sesiones(
    db: AsyncSession, user_id: int, *, motivo: Motivo
) -> None:
    """Deslogueo total del usuario: refresh **y** access.

    Las dos mitades importan. Revocar solo los refresh deja al atacante operando
    hasta `ACCESS_MINUTES`; mover solo `tokens_valid_from` lo deja sacar un
    access nuevo con el refresh que todavía tiene.
    """
    ahora = _ahora()
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=ahora, revoked_reason=motivo)
    )
    await db.execute(
        update(ListadoMedico)
        .where(ListadoMedico.ID == user_id)
        .values(tokens_valid_from=ahora)
    )
    await db.commit()
    invalidar_cache_corte(user_id)
    log.info("sesiones revocadas: user_id=%s motivo=%s", user_id, motivo)


# ── Validación del access token ──────────────────────────────────────────────

# Cache de `tokens_valid_from` por usuario, en memoria de cada proceso.
#
# Sin esto, TODA request autenticada hace un SELECT antes de empezar (más el
# ping del pool): dos viajes a la base por pedido, que pesan si la base está lejos
# del API. Con el cache, un usuario activo paga ese SELECT una vez cada
# `SESSION_CACHE_SECONDS`.
#
# El costo, aceptado: cortar una sesión desde OTRO proceso tarda hasta ese tiempo en
# hacer efecto. En el proceso que revoca (`revocar_sesiones`) el efecto es
# inmediato porque se descarta la entrada. Con `SESSION_CACHE_SECONDS=0` se
# desactiva y vuelve a consultarse en cada request.
_CACHE_MAX = 5000
# uid -> (timestamp de corte en segundos | None, vence_en (monotonic))
_cache_corte: dict[int, tuple[int | None, float]] = {}


def invalidar_cache_corte(user_id: int | None = None) -> None:
    """Descarta lo cacheado de un usuario (o de todos si no se indica)."""
    if user_id is None:
        _cache_corte.clear()
    else:
        _cache_corte.pop(user_id, None)


async def _corte_de(db: AsyncSession, uid: int) -> int | None:
    ttl = settings.SESSION_CACHE_SECONDS
    ahora = time.monotonic()
    if ttl > 0:
        hit = _cache_corte.get(uid)
        if hit is not None and hit[1] > ahora:
            return hit[0]

    corte = (
        await db.execute(
            select(ListadoMedico.tokens_valid_from).where(ListadoMedico.ID == uid)
        )
    ).scalar_one_or_none()
    ts = None if corte is None else int(corte.replace(tzinfo=timezone.utc).timestamp())

    if ttl > 0:
        if len(_cache_corte) >= _CACHE_MAX:
            _cache_corte.clear()
        _cache_corte[uid] = (ts, ahora + ttl)
    return ts


async def access_revocado(db: AsyncSession, uid: int | None, iat: int | None) -> bool:
    """`True` si el access token se emitió antes del último corte de sesiones.

    Se compara con `<` estricto y sobre segundos enteros: un token emitido en el
    mismo segundo que la revocación sobrevive. Es un margen de un segundo a
    favor del usuario, y evita desloguear a quien acaba de cambiar su contraseña
    y recibió el token nuevo en el mismo tick.

    Un token sin `uid` o sin `iat` no se puede evaluar y se deja pasar: son los
    emitidos antes de que existiera el claim, y viven como mucho
    `ACCESS_MINUTES`. El control de propiedad ya los rechaza aparte
    (`ownership.py` responde 401 `token_sin_identidad`).
    """
    if uid is None or iat is None:
        return False

    corte = await _corte_de(db, uid)
    if corte is None:
        return False

    return int(iat) < corte
