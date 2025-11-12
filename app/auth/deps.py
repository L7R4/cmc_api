from fastapi import Depends, Header, HTTPException
from jose import JWTError, ExpiredSignatureError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.database import get_db
from app.db.models import ListadoMedico, Role, UserRole
from app.utils.main import get_effective_permission_codes


# cmc_api/app/auth/deps.py
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from app.core.security import decode_token

async def get_current_user_with_scopes(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    user, scopes, _role = await get_current_user_with_scopes_and_role(authorization, db)
    return user, scopes, _role

bearer = HTTPBearer(auto_error=False)

def get_current_user(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    if not creds:
        raise HTTPException(401, "Falta token")

    try:
        data = decode_token(creds.credentials)  # verifica exp
    except ExpiredSignatureError:
        # 👇 esto es CLAVE para que el frontend active /auth/refresh
        raise HTTPException(401, "token_expired")
    except JWTError:
        raise HTTPException(401, "invalid_token")

    if data.get("type") != "access":
        raise HTTPException(401, "invalid_token_type")

    return {"nro_socio": data["sub"], "scopes": data.get("scopes", [])}


def require_scope(scope: str):
    def checker(user=Depends(get_current_user)):
        if scope not in (user.get("scopes") or []):
            raise HTTPException(403, "No tenés permiso")
        return user
    return checker


async def get_user_role(db: AsyncSession, user_id: int) -> str | None:
    """
    Devuelve el nombre del rol (Role.name) del usuario.
    Si el usuario tuviera más de un rol, devuelve el primero encontrado.
    """
    q = (
        select(Role.name)
        .select_from(UserRole)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id == user_id)
        .limit(1)
    )
    return (await db.execute(q)).scalar_one_or_none()


async def get_current_user_with_scopes_and_role(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Falta token Bearer")

    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token)
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido")

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Token inválido (tipo)")

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Token inválido (sub)")

    # Buscar usuario por ID (preferido) o por NRO_SOCIO como fallback
    user = None
    try:
        user = (await db.execute(
            select(ListadoMedico).where(ListadoMedico.ID == int(sub))
        )).scalar_one_or_none()
    except ValueError:
        user = None

    if not user:
        try:
            user = (await db.execute(
                select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == int(sub))
            )).scalar_one_or_none()
        except ValueError:
            user = None

    if not user:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")

    scopes = await get_effective_permission_codes(db, user.ID)

    # ① Preferimos el claim del token si viene; ② sino, lo leemos de DB
    role = payload.get("role") or await get_user_role(db, user.ID)
    if not role:
        raise HTTPException(status_code=409, detail="El usuario no tiene rol asignado")

    return user, scopes, role