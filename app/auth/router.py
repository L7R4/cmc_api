# cmc_api/app/auth/router.py
from fastapi import APIRouter, Depends, Header, Response, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select,text
from app.auth.deps import get_current_user, get_current_user_with_scopes, get_current_user_with_scopes_and_role, get_user_role
from app.db.database import get_db
from app.db.models import ListadoMedico
from app.core.security import create_access_token, decode_token
from app.core.passwords import verify_and_upgrade, verify_password, hash_password, validate_new_password
from app.core.config import settings
from app.core import ratelimit
from app.auth import sessions
import time, json, hmac, hashlib, base64, urllib.parse
from fastapi import HTTPException
import secrets
from urllib.parse import urlparse
from app.modules.medicos.schemas import UserEnvelope, UserOut
from app.auth.permissions import get_effective_permission_codes
from starlette.responses import RedirectResponse

router = APIRouter(prefix="/auth", tags=["auth"])
COOKIE_REFRESH = "refresh_token"
COOKIE_CSRF = "csrf_token"
COOKIE_LOGGED  = "CMC_LOGGED"   # <- cookie visible para Caddy


# Columnas legacy de especialidad del médico, en orden de prioridad (la principal NO
# lleva sufijo "1"). Mismo orden/criterio que _especialidades_medico del motor de precios.
_CAMPOS_ESPECIALIDAD = (
    "NRO_ESPECIALIDAD",
    "NRO_ESPECIALIDAD2",
    "NRO_ESPECIALIDAD3",
    "NRO_ESPECIALIDAD4",
    "NRO_ESPECIALIDAD5",
    "NRO_ESPECIALIDAD6",
)


def _especialidades_ordenadas(user) -> list[int]:
    """Especialidades cargadas del médico (ID_COLEGIO_ESPE) en orden de prioridad;
    omite slots vacíos (0/None)."""
    out: list[int] = []
    for campo in _CAMPOS_ESPECIALIDAD:
        esp = getattr(user, campo, None)
        if esp:
            out.append(int(esp))
    return out


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
async def login(
    body: LoginIn,
    req: Request,
    res: Response,
    db: AsyncSession = Depends(get_db),
):
    ns = body.nro_socio

    # Antes de tocar la base: si esta IP o esta cuenta ya agotaron los intentos,
    # se corta acá con 429. Ver app/core/ratelimit.py.
    ratelimit.chequear_login(req, ns)

    stmt = select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == ns)
    medico = (await db.execute(stmt)).scalar_one_or_none()
    if not medico:
        # Un socio inexistente cuenta como fallo: si no, enumerar qué números
        # de socio existen sería gratis.
        ratelimit.registrar_fallo(req, ns)
        raise HTTPException(401, "Usuario invalido")

    if getattr(medico, "EXISTE", "N") != "S":
        raise HTTPException(403, "Tu registro está pendiente de aprobacion")

    ok = await verify_and_upgrade(db, medico, body.password)
    if not ok:
        ratelimit.registrar_fallo(req, ns)
        raise HTTPException(401, "Credenciales invalidas")

    ratelimit.registrar_exito(req, ns)

    # ... emitir tokens usando SIEMPRE el mismo identificador
    sub = str(medico.NRO_SOCIO)
    scopes = await get_effective_permission_codes(db, medico.ID)
    role   = await get_user_role(db, medico.ID)

    access  = create_access_token(sub=sub, uid=medico.ID, scopes=scopes, role=role)
    # El refresh queda registrado en `refresh_tokens` antes de salir: es lo que
    # hace que el logout y el cambio de contraseña puedan cerrarlo de verdad.
    refresh = await sessions.emitir_refresh(db, medico, origen="web", request=req)
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
            "es_organizacion": medico.es_organizacion,
            "adherente": medico.adherente,
            # El front lo lee para mandar a la pantalla de cambio de contraseña
            # antes que a ningún otro lado: la cuenta todavía tiene la clave
            # inicial, que es pública. Ver app/core/passwords.py.
            "must_change_password": bool(medico.must_change_password),
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

    # 3) Rotar la sesión. Acá se valida que el `jti` esté vivo en la base: un
    #    refresh ya canjeado, o revocado por logout o por cambio de contraseña,
    #    no pasa de esta línea aunque su firma verifique.
    try:
        new_refresh = await sessions.rotar_refresh(
            db, medico, payload.get("jti"), origen="web", request=request
        )
    except sessions.SesionInvalida as e:
        # Se limpian las cookies: si el cliente sigue mandando un refresh muerto
        # en cada intento, el 401 se repite para siempre. Borrarlas lo manda al
        # login, que es lo único que puede resolverlo.
        response.delete_cookie(COOKIE_REFRESH, **_cookie_args("/auth", True))
        response.delete_cookie(COOKIE_CSRF,    **_cookie_args("/",     False))
        response.delete_cookie(COOKIE_LOGGED,  **_cookie_args("/",     False))
        raise HTTPException(401, e.codigo)

    # 4) Access nuevo, con los scopes releídos de la base: es acá donde un cambio
    #    de permisos se hace efectivo.
    scopes = await get_effective_permission_codes(db, medico.ID)
    role = await get_user_role(db, medico.ID)
    access = create_access_token(sub=str(sub), uid=medico.ID, scopes=scopes, role=role)

    max_age = 60 * 60 * 24 * settings.REFRESH_DAYS
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
            "es_organizacion": medico.es_organizacion,
            "adherente": medico.adherente,
            "must_change_password": bool(medico.must_change_password),
        },
    }

@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Cierra la sesión de verdad, no solo en este navegador.

    Antes esto borraba tres cookies y nada más: el refresh token seguía siendo
    válido 15 días para cualquiera que tuviera una copia — que es exactamente el
    caso en el que uno cierra sesión. Ahora se revoca el `jti` en la base.

    Solo esta sesión y no todas, a propósito: cerrar sesión en la computadora
    del Colegio no tiene por qué desloguear el teléfono. Para cerrarlas todas
    está el cambio de contraseña.
    """
    rt = request.cookies.get(COOKIE_REFRESH)
    if rt:
        try:
            payload = decode_token(rt)
            await sessions.revocar_por_jti(db, payload.get("jti"), motivo="logout")
        except Exception:
            # Un refresh vencido o ilegible no impide cerrar sesión: las cookies
            # se borran igual y ese token tampoco servía.
            pass

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
async def change_password(
    body: ChangePasswordIn,
    req: Request,
    res: Response,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cambia la contraseña y **cierra todas las sesiones del usuario** (A7).

    Uno cambia la contraseña por dos motivos: porque tocaba, o porque cree que
    alguien se la sacó. En el segundo, dejar las sesiones abiertas vacía la
    operación de sentido — el atacante conserva su refresh token durante 15 días
    y su access hasta 15 minutos, y la contraseña nueva no le hace nada.

    Se cierran todas, incluida la del que hace el cambio: el front recibe 200 y
    tiene que mandar al login. Las cookies se borran acá mismo para que no quede
    un refresh muerto dando 401 en loop.
    """
    try:
        new_password = validate_new_password(body.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e))

    nro_socio = int(user["nro_socio"])

    # Exige token válido, pero igual permite adivinar `old_password` a fuerza
    # bruta: un access token robado alcanzaría para tomar la cuenta del todo.
    ratelimit.chequear_login(req, nro_socio)

    stmt = select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == nro_socio)
    medico = (await db.execute(stmt)).scalar_one_or_none()
    if not medico:
        raise HTTPException(404, "Usuario no encontrado")
    if not verify_password(body.old_password, medico.hashed_password):
        ratelimit.registrar_fallo(req, nro_socio)
        raise HTTPException(400, "La contraseña actual es incorrecta")

    ratelimit.registrar_exito(req, nro_socio)
    medico.hashed_password = hash_password(new_password)
    # La cuenta deja de estar con la contraseña inicial pública.
    medico.must_change_password = False
    await db.commit()

    # Todos los refresh a revocado y `tokens_valid_from = NOW()`, que invalida
    # los access ya emitidos sin esperar a que venzan.
    await sessions.revocar_sesiones(db, medico.ID, motivo="cambio_password")

    res.delete_cookie(COOKIE_REFRESH, **_cookie_args("/auth", True))
    res.delete_cookie(COOKIE_CSRF,    **_cookie_args("/",     False))
    res.delete_cookie(COOKIE_LOGGED,  **_cookie_args("/",     False))

    # `relogin` le dice al front que mande al login sin intentar /auth/refresh.
    return {"ok": True, "relogin": True}

@router.get("/me", response_model=UserEnvelope)
async def get_me(dep = Depends(get_current_user_with_scopes_and_role)):
    user, scopes, role = dep
    out = UserOut(
        id=user.ID,
        nro_socio=user.NRO_SOCIO,
        nombre=getattr(user, "NOMBRE", None),
        scopes=scopes,
        role=role,
        es_organizacion=getattr(user, "es_organizacion", False),
        adherente=getattr(user, "adherente", False),
        especialidades=_especialidades_ordenadas(user),
        must_change_password=bool(getattr(user, "must_change_password", False)),
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
        "es_organizacion": int(getattr(user, "es_organizacion", 0) or 0),
        "adherente": bool(getattr(user, "adherente", False)),
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
    request: Request,
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

    # Scopes y rol SIEMPRE desde la base, nunca desde el payload (A8).
    #
    # Antes esto era `data.get("scopes") or <consulta>`: el sistema legacy
    # mandaba una lista de permisos y la API la copiaba tal cual al JWT que
    # emitía. La firma HMAC garantiza que el payload no se modificó en tránsito,
    # pero **no** garantiza que su contenido sea cierto: quien pueda firmar
    # —el legacy, o cualquiera con `LEGACY_SSO_SECRET`— fabricaba un payload con
    # `"scopes": ["rbac:gestionar", ...]` y la API le creía. Un secreto de
    # autenticación pasaba a valer también como autorización, que es un nivel de
    # confianza que no le corresponde.
    #
    # Recomputarlo cuesta las dos consultas que ya hace el login normal, y deja
    # a la base como única fuente de verdad de quién puede qué.
    scopes = await get_effective_permission_codes(db, medico.ID)
    role   = await get_user_role(db, medico.ID)

    access  = create_access_token(sub=str(medico.NRO_SOCIO), uid=medico.ID, scopes=scopes, role=role)
    refresh = await sessions.emitir_refresh(db, medico, origen="web", request=request)
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