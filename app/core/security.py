# cmc_api/app/core/security.py
from datetime import datetime, timedelta, timezone
from jose import jwt
from app.core.config import settings

def _now_utc():
    return datetime.now(timezone.utc)

def _ts(dt: datetime) -> int:
    # convierte a epoch seconds (int)
    return int(dt.timestamp())

def create_access_token(*, sub: str | int, uid: int | None = None, scopes: list[str] = None, role: str | None = None, expires_delta: timedelta | None = None):
    """Access token.

    `sub` es el NRO_SOCIO (la credencial pública) y `uid` es `ListadoMedico.ID`
    (la PK interna). Van los dos porque la base usa las dos convenciones y el
    control de propiedad necesita comparar contra la correcta: `medicos` y
    `padrones` indexan por ID, `validaciones` y `DetalleLiquidacion` por
    NRO_SOCIO. Resolver uno desde el otro costaría una query por request.
    Ver app/auth/ownership.py y docs/api/RBAC_PROPUESTA.md §6.2.
    """
    now = _now_utc()
    exp = now + timedelta(minutes=settings.ACCESS_MINUTES)
    payload = {
        "sub": str(sub),
        "uid": int(uid) if uid is not None else None,
        "type": "access",
        "scopes": scopes or [],
        "role": role,
        "iat": _ts(now),            # emitido en
        "nbf": _ts(now),            # no válido antes de
        "exp": _ts(exp),            # vence en
    }
    return jwt.encode(payload, settings.JWT_SECRET.get_secret_value(), algorithm=settings.JWT_ALG)

def create_refresh_token(sub: str, jti: str):
    now = _now_utc()
    exp = now + timedelta(days=settings.REFRESH_DAYS)
    payload = {
        "sub": sub,
        "type": "refresh",
        "jti": jti,
        "iat": _ts(now),
        "nbf": _ts(now),
        "exp": _ts(exp),
    }
    return jwt.encode(payload, settings.JWT_SECRET.get_secret_value(), algorithm=settings.JWT_ALG)

def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET.get_secret_value(), algorithms=[settings.JWT_ALG], options={"leeway": 15})
