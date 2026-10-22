from fastapi import Depends, Header, HTTPException
from jose import JWTError, ExpiredSignatureError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.database import get_db
from app.db.models import ListadoMedico
from app.auth.identidad import SinAcceso, exigir_acceso, rol_principal
from app.auth.permissions import get_effective_permission_codes


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

    # `uid` es ListadoMedico.ID; puede venir None en tokens emitidos antes de que
    # el claim existiera. Como el access vive ACCESS_MINUTES, la ventana de
    # transición es de minutos, no de días: el refresh relee la base y emite uno
    # nuevo con el claim. app/auth/ownership.py maneja el None explícitamente.
    return {
        "nro_socio": data["sub"],
        "uid": data.get("uid"),
        "scopes": data.get("scopes", []),
        # Lo usa app/auth/sessions.py::access_revocado para comparar contra
        # `listado_medico.tokens_valid_from` y cortar sesiones sin esperar al exp.
        "iat": data.get("iat"),
    }


def usuario_opcional(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    """El usuario del token, o `None` si no vino token o no sirve.

    Para los endpoints **públicos con respuesta reducida**: los del portal, que
    tiene que poder ver un visitante anónimo, pero que devuelven más datos a
    quien está autenticado. `GET /api/obras_social/` es el caso: un visitante
    necesita el nombre de la obra social, y el personal del Colegio necesita
    además el CUIT, los contactos y las condiciones del convenio.

    Nunca lanza: un token vencido o inválido es lo mismo que no traer ninguno.
    Eso es correcto acá y **sería un agujero en cualquier otro lado** — por eso
    no reemplaza a `get_current_user`, que sigue siendo el que exige token.
    """
    if not creds:
        return None
    try:
        data = decode_token(creds.credentials)
    except (ExpiredSignatureError, JWTError):
        return None
    if data.get("type") != "access":
        return None
    return {
        "nro_socio": data.get("sub"),
        "uid": data.get("uid"),
        "scopes": data.get("scopes", []),
        "iat": data.get("iat"),
    }


def require_scope(scope: str):
    def checker(user=Depends(get_current_user)):
        if scope not in (user.get("scopes") or []):
            raise HTTPException(403, "No tenés permiso")
        return user
    return checker


async def get_user_role(db: AsyncSession, user_id: int) -> str | None:
    """Rol principal del usuario: con varios, el de más alcance (ver `identidad.JERARQUIA_ROLES`)."""
    return await rol_principal(db, user_id)


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

    # `sub` es SIEMPRE el NRO_SOCIO — lo emiten así los tres puntos que crean
    # access tokens (/auth/login, /auth/refresh y el login y refresh móviles).
    # Acá había una segunda consulta idéntica como supuesto "fallback por ID",
    # que consultaba la misma columna y por lo tanto no podía encontrar nada que
    # la primera no hubiera encontrado ya. Ver A10.
    try:
        user = (await db.execute(
            select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == int(sub))
        )).scalar_one_or_none()
    except ValueError:
        user = None

    if not user:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")

    # El rol se lee de la base y no del claim del token, igual que los scopes: un
    # cambio de rol hecho por SQL no tiene que esperar a que venza el access.
    role = await rol_principal(db, user.ID)
    try:
        exigir_acceso(user, role)
    except SinAcceso as e:
        # 401 para la cuenta inactiva: el front intenta /auth/refresh, que también
        # la rechaza, y termina en el login con el motivo. Sin rol es 403.
        raise HTTPException(401 if e.codigo == "cuenta_inactiva" else 403, e.mensaje)

    scopes = await get_effective_permission_codes(db, user.ID)
    return user, scopes, role