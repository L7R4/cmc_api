# cmc_api/app/core/security.py
from datetime import datetime, timedelta, timezone
from jose import jwt
from app.core.config import settings

def _now_utc():
    return datetime.now(timezone.utc)

def _ts(dt: datetime) -> int:
    # convierte a epoch seconds (int)
    return int(dt.timestamp())

def create_access_token(*, sub: str | int, scopes: list[str] = None, role: str | None = None, expires_delta: timedelta | None = None):
    now = _now_utc()
    exp = now + timedelta(minutes=settings.ACCESS_MINUTES)
    payload = {
        "sub": str(sub),
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
