# cmc_api/app/auth/router.py
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import sessions
from app.auth.deps import get_current_user, get_current_user_with_scopes_and_role
from app.auth.identidad import SinAcceso, construir_usuario
from app.core import ratelimit
from app.core.config import settings
from app.core.passwords import hash_password, validate_new_password, verify_and_upgrade, verify_password
from app.core.security import create_access_token, decode_token
from app.db.database import get_db
from app.db.models import ListadoMedico
from app.modules.medicos.schemas import UserEnvelope

router = APIRouter(prefix="/auth", tags=["auth"])
COOKIE_REFRESH = "refresh_token"
COOKIE_CSRF = "csrf_token"


class LoginIn(BaseModel):
    nro_socio: int
    password: str  # = matricula_prov (inicialmente)

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


def _borrar_cookies_sesion(response: Response) -> None:
    response.delete_cookie(COOKIE_REFRESH, **_cookie_args("/auth", True))
    response.delete_cookie(COOKIE_CSRF,    **_cookie_args("/",     False))


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

    ok = await verify_and_upgrade(db, medico, body.password)
    if not ok:
        ratelimit.registrar_fallo(req, ns)
        raise HTTPException(401, "Credenciales invalidas")

    ratelimit.registrar_exito(req, ns)

    # Recién con la contraseña verificada se dice por qué no entra: antes, el
    # estado de la cuenta se informaba a cualquiera que probara un número de socio.
    try:
        usuario, scopes, role = await construir_usuario(db, medico)
    except SinAcceso as e:
        raise HTTPException(403, e.mensaje)

    access  = create_access_token(sub=str(medico.NRO_SOCIO), uid=medico.ID, scopes=scopes, role=role)
    # El refresh queda registrado en `refresh_tokens` antes de salir: es lo que
    # hace que el logout y el cambio de contraseña puedan cerrarlo de verdad.
    refresh = await sessions.emitir_refresh(db, medico, origen="web", request=req)
    csrf    = secrets.token_urlsafe(16)

    max_age = 60 * 60 * 24 * settings.REFRESH_DAYS

    # Cookies:
    res.set_cookie(COOKIE_REFRESH, refresh, **_cookie_args("/auth", True,  max_age))
    res.set_cookie(COOKIE_CSRF,    csrf,    **_cookie_args("/",     False, max_age))

    return {"access_token": access, "token_type": "bearer", "user": usuario}

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

    # Antes de rotar: una cuenta que dejó de estar activa, o se quedó sin rol, no
    # recibe un refresh nuevo. Se borran las cookies por lo mismo que abajo.
    try:
        usuario, scopes, role = await construir_usuario(db, medico)
    except SinAcceso as e:
        _borrar_cookies_sesion(response)
        raise HTTPException(401, e.mensaje)

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
        _borrar_cookies_sesion(response)
        raise HTTPException(401, e.codigo)

    # 4) Access nuevo, con los scopes releídos de la base (construir_usuario):
    #    es acá donde un cambio de permisos se hace efectivo.
    access = create_access_token(sub=str(sub), uid=medico.ID, scopes=scopes, role=role)

    max_age = 60 * 60 * 24 * settings.REFRESH_DAYS
    new_csrf = secrets.token_urlsafe(16)

    response.set_cookie(COOKIE_REFRESH, new_refresh, **_cookie_args("/auth", True,  max_age))
    response.set_cookie(COOKIE_CSRF,    new_csrf,    **_cookie_args("/",     False, max_age))

    return {"access_token": access, "token_type": "bearer", "user": usuario}

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

    _borrar_cookies_sesion(response)
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

    _borrar_cookies_sesion(res)

    # `relogin` le dice al front que mande al login sin intentar /auth/refresh.
    return {"ok": True, "relogin": True}

@router.get("/me", response_model=UserEnvelope)
async def get_me(
    dep = Depends(get_current_user_with_scopes_and_role),
    db: AsyncSession = Depends(get_db),
):
    # La dependencia ya validó token, cuenta activa y rol.
    user, _scopes, _role = dep
    usuario, _, _ = await construir_usuario(db, user)
    return {"user": usuario}

