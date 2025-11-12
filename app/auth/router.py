# cmc_api/app/auth/router.py
from fastapi import APIRouter, Depends, Header, Response, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select,text
from app.auth.deps import get_current_user_with_scopes, get_current_user_with_scopes_and_role, get_user_role
from app.db.database import get_db
from app.db.models import ListadoMedico
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.core.passwords import verify_and_upgrade, verify_password, hash_password
from app.core.config import settings
import time, json, hmac, hashlib, base64, urllib.parse
from fastapi import HTTPException
import secrets

from app.schemas.medicos_schema import UserEnvelope, UserOut
from app.utils.main import get_effective_permission_codes

router = APIRouter(prefix="/auth", tags=["auth"])
COOKIE_REFRESH = "refresh_token"
COOKIE_CSRF = "csrf_token"

class LoginIn(BaseModel):
    nro_socio: int
    password: str  # = matricula_prov (inicialmente)

@router.post("/login")
async def login(body: LoginIn, res: Response, db: AsyncSession = Depends(get_db)):
    # Buscar por NRO_SOCIO
    stmt = select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == body.nro_socio)
    medico = (await db.execute(stmt)).scalar_one_or_none()
    if not medico:
        raise HTTPException(401, "Usuario inválido")

    if getattr(medico, "EXISTE", "N") != "S":
        raise HTTPException(403, "Tu registro está pendiente de aprobación")
    
    ok = await verify_and_upgrade(db, medico, body.password)
    if not ok:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    if not verify_password(body.password, medico.hashed_password):
        raise HTTPException(401, "Credenciales inválidas")
    
    

    # 🔴 NUEVO: permisos efectivos desde RBAC
    scopes = await get_effective_permission_codes(db, medico.ID)
    role = await get_user_role(db, medico.ID)

    access = create_access_token(sub=str(medico.NRO_SOCIO), scopes=scopes,role=role)
    jti = secrets.token_hex(16)
    refresh = create_refresh_token(sub=str(medico.NRO_SOCIO), jti=jti)
    csrf = secrets.token_urlsafe(16)

    # cookies (igual que antes)
    COOKIE_SAMESITE = "none" if settings.COOKIE_SECURE else "lax"
    common = dict(
        httponly=True, samesite=COOKIE_SAMESITE, secure=settings.COOKIE_SECURE,
        path="/auth", max_age=60*60*24*settings.REFRESH_DAYS
    )
    res.set_cookie(COOKIE_REFRESH, refresh, **common)
    res.set_cookie(COOKIE_CSRF, csrf, httponly=False, samesite=COOKIE_SAMESITE,
                   secure=settings.COOKIE_SECURE, path="/",
                   max_age=60*60*24*settings.REFRESH_DAYS)

    return {
        "access_token": access,
        "token_type": "bearer",
        "user": {"id": medico.ID, "nro_socio": medico.NRO_SOCIO, "nombre": medico.NOMBRE, "scopes": scopes, "role": role},
    }

@router.post("/refresh")
async def refresh_token(
    request: Request,
    response: Response,
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    db: AsyncSession = Depends(get_db),
):
    print(">> cookies:", request.cookies)           # ¿Viene refresh_token?
    print(">> header X-CSRF-Token:", x_csrf_token)  # ¿Llega?
    rt = request.cookies.get("refresh_token")
    if not rt:
        raise HTTPException(401, "Falta refresh_token")

    # 1) CSRF: header debe igualar a cookie 'csrf_token'
    csrf_cookie = request.cookies.get("csrf_token")
    if not x_csrf_token or not csrf_cookie or x_csrf_token != csrf_cookie:
        raise HTTPException(401, "CSRF inválido")

    # 2) Decodificar refresh
    try:
        payload = decode_token(rt)
    except Exception:
        raise HTTPException(401, "Refresh inválido")

    if payload.get("type") != "refresh":
        raise HTTPException(401, "Tipo de token no válido")

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(401, "Refresh sin sub")

    medico = None
    # intentar como ID interno
    try:
        medico = (await db.execute(
            select(ListadoMedico).where(ListadoMedico.ID == int(sub))
        )).scalar_one_or_none()
    except ValueError:
        medico = None

    # si no lo encontramos, probar como NRO_SOCIO
    if not medico:
        try:
            medico = (await db.execute(
                select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == int(sub))
            )).scalar_one_or_none()
        except ValueError:
            medico = None

    if not medico:
        raise HTTPException(401, "Usuario no encontrado")

    scopes = await get_effective_permission_codes(db, medico.ID)
    role = await get_user_role(db, medico.ID)
    if not role:
        raise HTTPException(409, "El usuario no tiene rol asignado")
    # mantené el mismo 'sub' que venía en el refresh para compatibilidad
    access = create_access_token(sub=str(sub), scopes=scopes, role=role)

    # 4) (Dev) no rotar refresh para evitar bloqueo de Set-Cookie
    #    Si quieres rotarlo en prod, aquí generas y seteas uno nuevo.
    COOKIE_SAMESITE = "none" if settings.COOKIE_SECURE else "lax"

    # Reafirma csrf cookie (opcional)
    response.set_cookie(
        "csrf_token", csrf_cookie,
        httponly=False, samesite=COOKIE_SAMESITE, secure=settings.COOKIE_SECURE, path="/",
        max_age=60*60*24*settings.REFRESH_DAYS,
    )

    return {"access_token": access, "token_type": "bearer"}


@router.post("/logout")
async def logout(response: Response):
    COOKIE_SAMESITE = "none" if settings.COOKIE_SECURE else "lax"

    # Borrar cookies (coincidiendo path/flags)
    response.delete_cookie(
        "refresh_token", path="/auth", samesite=COOKIE_SAMESITE, secure=settings.COOKIE_SECURE, httponly=True
    )
    response.delete_cookie(
        "csrf_token", path="/", samesite=COOKIE_SAMESITE, secure=settings.COOKIE_SECURE, httponly=False
    )
    return {"ok": True}

class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str

@router.post("/change-password")
async def change_password(body: ChangePasswordIn, req: Request, db: AsyncSession = Depends(get_db)):
    # Requiere estar logueado: decodificamos access desde header en un paso simple:
    # (Si preferís, usá get_current_user de deps.py)
    auth = req.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Falta token")
    data = decode_token(auth.split()[1])
    if data.get("type") != "access":
        raise HTTPException(401, "Token inválido")

    nro_socio = int(data["sub"])
    stmt = select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == nro_socio)
    medico = (await db.execute(stmt)).scalar_one_or_none()
    if not medico:
        raise HTTPException(404, "Usuario no encontrado")
    if not verify_password(body.old_password, medico.hashed_password):
        raise HTTPException(400, "La contraseña actual es incorrecta")

    medico.hashed_password = hash_password(body.new_password)
    await db.commit()
    return {"ok": True}

@router.get("/me", response_model=UserEnvelope)
async def get_me(dep = Depends(get_current_user_with_scopes_and_role)):
    user, scopes, role = dep
    out = UserOut(
        id=user.ID,
        nro_socio=user.NRO_SOCIO,
        nombre=getattr(user, "NOMBRE", None),
        scopes=scopes,
        role=role,          
    )
    return {"user": out}


@router.get("/legacy/sso-link")
async def legacy_sso_link(
    next: str = "/",
    dep = Depends(get_current_user_with_scopes_and_role),
):
    user, scopes, role = dep
    # print("USUARIO")
    # print(user)
    # print(scopes)
    # print(role)


    # if not any(s in scopes for s in ["legacy:access", "legacy:facturista", "facturista","facturas:ver", "medicos:ver_solo_perfil"]):
    #     raise HTTPException(403, "No tenés permiso para el legacy")

    if not settings.LEGACY_BASE_URL or not settings.LEGACY_SSO_SECRET:
        raise HTTPException(503, "SSO del legacy no está configurado")

    now = int(time.time())
    exp = now + 300  # 5 minutos de validez

    payload = {
        "id": int(user.ID) if hasattr(user, "ID") else None,
        "nro_socio": int(user.NRO_SOCIO),
        "nombre": getattr(user, "NOMBRE", None),
        "scopes": scopes,
        "role": role,
        "iat": now,
        "exp": exp,
    }
    body = base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii").rstrip("=")
    key = settings.LEGACY_SSO_SECRET.get_secret_value().encode("utf-8")
    sig = base64.urlsafe_b64encode(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest()).decode("ascii").rstrip("=")

    next_qs = urllib.parse.quote(next, safe="/:?=&")
    url = f"{settings.LEGACY_BASE_URL}{settings.LEGACY_SSO_PATH}?payload={body}&sig={sig}&next={next_qs}"
    return {"url": url}
