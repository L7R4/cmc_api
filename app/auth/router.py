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
from urllib.parse import urlparse
from app.schemas.medicos_schema import UserEnvelope, UserOut
from app.utils.main import get_effective_permission_codes
from starlette.responses import RedirectResponse

router = APIRouter(prefix="/auth", tags=["auth"])
COOKIE_REFRESH = "refresh_token"
COOKIE_CSRF = "csrf_token"
COOKIE_LOGGED  = "CMC_LOGGED"   # <- cookie visible para Caddy


class LoginIn(BaseModel):
    nro_socio: int
    password: str  # = matricula_prov (inicialmente)

def _b64url_decode(data: str) -> bytes:
    pad = '=' * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + pad).encode('ascii'))

def _cookie_args(path: str, http_only: bool, max_age: int | None = None) -> dict:
    """Kwargs consistentes para set_cookie/delete_cookie."""
    d = {
        "httponly": http_only,
        "samesite": settings.COOKIE_SAMESITE,   # 'lax'
        "secure": settings.COOKIE_SECURE,
        "path": path,
    }
    if max_age is not None:
        d["max_age"] = max_age
    if settings.COOKIE_DOMAIN:
        d["domain"] = settings.COOKIE_DOMAIN
    return d

@router.post("/login")
async def login(body: LoginIn, res: Response, db: AsyncSession = Depends(get_db)):
    ns = body.nro_socio
    stmt = select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == ns)
    medico = (await db.execute(stmt)).scalar_one_or_none()
    if not medico:
        raise HTTPException(401, "Usuario invalido")

    if getattr(medico, "EXISTE", "N") != "S":
        raise HTTPException(403, "Tu registro está pendiente de aprobacion")

    ok = await verify_and_upgrade(db, medico, body.password)
    if not ok:
        raise HTTPException(401, "Credenciales invalidas")

    # ... emitir tokens usando SIEMPRE el mismo identificador
    sub = str(medico.NRO_SOCIO)
    scopes = await get_effective_permission_codes(db, medico.ID)
    role   = await get_user_role(db, medico.ID)

    access  = create_access_token(sub=sub, scopes=scopes, role=role)
    jti     = secrets.token_hex(16)
    refresh = create_refresh_token(sub=sub, jti=jti)
    csrf    = secrets.token_urlsafe(16)

    max_age = 60 * 60 * 24 * settings.REFRESH_DAYS

    # Cookies:
    res.set_cookie(COOKIE_REFRESH, refresh, **_cookie_args("/auth", True,  max_age))
    res.set_cookie(COOKIE_CSRF,    csrf,    **_cookie_args("/",     False, max_age))

    # Cookie visible para Caddy (marca "logueado"):
    res.set_cookie(COOKIE_LOGGED,  "1",     **_cookie_args("/",     False, max_age))


    return {
        "access_token": access,
        "token_type": "bearer",
        "user": {
            "id": medico.ID,
            "nro_socio": sub,
            "nombre": medico.NOMBRE,
            "scopes": scopes,
            "role": role,
        },
    }

@router.post("/refresh")
async def refresh_token(
    request: Request,
    response: Response,
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    db: AsyncSession = Depends(get_db),
):
    # 0) Cookies y CSRF
    rt = request.cookies.get(COOKIE_REFRESH)
    if not rt:
        raise HTTPException(401, "Falta refresh_token")

    csrf_cookie = request.cookies.get(COOKIE_CSRF)
    if not x_csrf_token or not csrf_cookie or x_csrf_token != csrf_cookie:
        raise HTTPException(401, "CSRF inválido")

    # 1) Decodificar y validar refresh
    try:
        payload = decode_token(rt)
    except Exception:
        raise HTTPException(401, "Refresh inválido")

    if not payload or payload.get("type") != "refresh":
        raise HTTPException(401, "Token no es refresh")

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(401, "Refresh sin sub")

    # 2) Usuario + permisos/rol
    medico = (await db.execute(select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == int(sub)))).scalar_one_or_none()
    if not medico:
        raise HTTPException(401, "Usuario no encontrado")

    scopes = await get_effective_permission_codes(db, medico.ID)
    role = await get_user_role(db, medico.ID)

    # 3) Emitir access (siempre) y (opcional) rotar refresh+csrf
    access = create_access_token(sub=str(sub), scopes=scopes, role=role)


    max_age = 60 * 60 * 24 * settings.REFRESH_DAYS
    jti = secrets.token_hex(16)
    new_refresh = create_refresh_token(sub=str(sub), jti=jti)
    new_csrf = secrets.token_urlsafe(16)

    response.set_cookie(COOKIE_REFRESH, new_refresh, **_cookie_args("/auth", True,  max_age))
    response.set_cookie(COOKIE_CSRF,    new_csrf,    **_cookie_args("/",     False, max_age))
    # Refrescar "logueado"
    response.set_cookie(COOKIE_LOGGED,  "1",         **_cookie_args("/",     False, max_age))

    return {
        "access_token": access,
        "token_type": "bearer",
        "user": {
            "id": int(medico.ID),
            "nro_socio": int(sub),
            "nombre": getattr(medico, "NOMBRE", None),
            "scopes": scopes,
            "role": role,
        },
    }

@router.post("/logout")
async def logout(response: Response):
    # Borrar refresh (path=/auth, httponly)
    response.delete_cookie(COOKIE_REFRESH, **_cookie_args("/auth", True))
    # Borrar csrf (path=/, no-httponly)
    response.delete_cookie(COOKIE_CSRF,    **_cookie_args("/",     False))
    # Borrar marca de logueado (visible)
    response.delete_cookie(COOKIE_LOGGED,  **_cookie_args("/",     False))
    return {"ok": True}


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str

@router.post("/change-password")
async def change_password(body: ChangePasswordIn, req: Request, db: AsyncSession = Depends(get_db)):
    # Requiere estar logueado: decodificamos access desde header en un paso simple:
    # (Si preferÃ­s, usÃ¡ get_current_user de deps.py)
    auth = req.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Falta token")
    data = decode_token(auth.split()[1])
    if data.get("type") != "access":
        raise HTTPException(401, "Token invÃ¡lido")

    nro_socio = int(data["sub"])
    stmt = select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == nro_socio)
    medico = (await db.execute(stmt)).scalar_one_or_none()
    if not medico:
        raise HTTPException(404, "Usuario no encontrado")
    if not verify_password(body.old_password, medico.hashed_password):
        raise HTTPException(400, "La contrasena actual es incorrecta")

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

    if not settings.LEGACY_BASE_URL or not settings.LEGACY_SSO_SECRET:
        raise HTTPException(503, "SSO del legacy no estÃ¡ configurado")

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


def _safe_front_redirect(next_path: str | None) -> str:
    # FRONT_BASE_URL obligatorio para construir absolutos
    base = (settings.FRONT_BASE_URL or "https://colegiomedicocorrientes.com").rstrip("/")
    default = f"{base}/panel/dashboard"

    # lista permitida desde tu método
    allowed_hosts = set(settings.ALLOWED_FRONT_HOSTS_LIST() if settings.ALLOWED_FRONT_HOSTS else [])
    # añade el host del FRONT_BASE_URL por si no lo pusiste en ALLOWED_FRONT_HOSTS
    try:
        fb = urlparse(base)
        if fb.hostname:
            allowed_hosts.add(fb.hostname)
    except Exception:
        pass

    if not next_path:
        return default

    # Si es relativo, lo pegamos al apex del front
    if next_path.startswith("/"):
        return base + next_path

    # Si viene absoluto, sólo permití hosts conocidos
    try:
        u = urlparse(next_path)
        if u.scheme in {"https", "http"} and u.hostname in allowed_hosts:
            return next_path
    except Exception:
        pass

    return default

@router.get("/legacy-sso-accept")
async def legacy_sso_accept(
    payload: str,
    sig: str,
    next: str = "/panel/dashboard",
    db: AsyncSession = Depends(get_db),
):
    """
    Acepta un SSO iniciado DESDE el legacy hacia el NUEVO sistema.
    1) Verifica HMAC (mismo LEGACY_SSO_SECRET que usa /auth/legacy/sso-link).
    2) Decodifica payload (base64url(JSON)) y valida exp.
    3) Emite/Setea refresh y csrf cookies igual que /auth/login.
    4) Redirige a `next` (por defecto /panel/dashboard).
    """
    if not settings.LEGACY_SSO_SECRET:
        raise HTTPException(500, "LEGACY_SSO_SECRET no configurado")

    # 1) verificar firma
    key = settings.LEGACY_SSO_SECRET.get_secret_value().encode("utf-8")
    expected = base64.urlsafe_b64encode(hmac.new(key, payload.encode("ascii"), hashlib.sha256).digest()).decode("ascii").rstrip("=")
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(401, "Firma inválida")

    # 2) decodificar payload
    try:
        data = json.loads(_b64url_decode(payload))
    except Exception:
        raise HTTPException(400, "Payload inválido")

    now = int(time.time())
    if int(data.get("exp", 0)) < now:
        raise HTTPException(401, "Payload expirado")

    # Puede venir sub/nro_socio o id. Soportá ambos.
    sub = data.get("nro_socio") or data.get("sub")
    if not sub:
        raise HTTPException(400, "Payload sin sub/nro_socio")

    # buscar usuario (por ID o NRO_SOCIO)
    medico = None
    try:
        medico = (await db.execute(select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == int(sub)))).scalar_one_or_none()
    except:
        pass
    if not medico and data.get("id"):
        try:
            medico = (await db.execute(select(ListadoMedico).where(ListadoMedico.ID == int(data["id"])))).scalar_one_or_none()
        except:
            pass
    if not medico:
        raise HTTPException(401, "Usuario no encontrado")

    # scopes/role desde payload o recomputados
    scopes = data.get("scopes") or await get_effective_permission_codes(db, medico.ID)
    role   = data.get("role")   or await get_user_role(db, medico.ID)

    access  = create_access_token(sub=str(medico.NRO_SOCIO), scopes=scopes, role=role)
    jti     = secrets.token_hex(16)
    refresh = create_refresh_token(sub=str(medico.NRO_SOCIO), jti=jti)
    csrf    = secrets.token_urlsafe(16)

    COOKIE_SAMESITE = "none" if settings.COOKIE_SECURE else "lax"

    # cookies con domain opcional
    common = dict(
        httponly=True, samesite=COOKIE_SAMESITE, secure=settings.COOKIE_SECURE,
        path="/auth", max_age=60*60*24*settings.REFRESH_DAYS
    )
    if settings.COOKIE_DOMAIN:
        common["domain"] = settings.COOKIE_DOMAIN

    csrf_kwargs = dict(
        httponly=False, samesite=COOKIE_SAMESITE, secure=settings.COOKIE_SECURE,
        path="/", max_age=60*60*24*settings.REFRESH_DAYS
    )
    if settings.COOKIE_DOMAIN:
        csrf_kwargs["domain"] = settings.COOKIE_DOMAIN

    redirect_url = _safe_front_redirect(next)
    resp = RedirectResponse(url=redirect_url, status_code=302)
    resp.set_cookie("refresh_token", refresh, **common)
    resp.set_cookie("csrf_token", csrf, **csrf_kwargs)
    # opcional: mandar el access en cookie no-HttpOnly si querés rehidratar más rápido (yo no lo recomiendo)
    return resp