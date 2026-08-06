"""Default cerrado: todo endpoint exige token salvo los que estén acá.

El problema que resuelve esto no es que falten dependencias en 207 endpoints, es
que el default era abierto: olvidarse de un `Depends` dejaba el endpoint público
en silencio. Con el default invertido, olvidarse produce un 401 visible.

Cómo funciona: `enforce_authz` (en `app/auth/authz.py`) se registra como
dependencia a nivel app (`FastAPI(dependencies=[...])`), con lo que FastAPI la
inyecta en **todas** las rutas. Mira el *template* de la ruta que resolvió
Starlette (`/api/medicos/{medico_id}`, no `/api/medicos/42`) y solo deja pasar
sin token las que figuran acá.

Este archivo responde "¿hace falta token?". El scope que además exige cada ruta
vive en `app/auth/authz.py::SCOPES_POR_RUTA`.

Para hacer público un endpoint nuevo hay que agregarlo acá, y eso se ve en el
diff. Esa visibilidad es el punto: la lista solo debería achicarse.
"""

# (método, template de ruta). El template es `route.path`, con los parámetros
# sin resolver — así `/api/medicos/{medico_id}` no depende del ID concreto.
PUBLIC_ROUTES: set[tuple[str, str]] = {
    # ── Autenticación web ────────────────────────────────────────────────────
    ("POST", "/auth/login"),
    ("POST", "/auth/refresh"),      # se autentica con la cookie + CSRF, no con Bearer
    ("POST", "/auth/logout"),       # tiene que funcionar con el access ya vencido
    ("GET", "/auth/legacy-sso-accept"),  # entrada desde el legacy, validada por HMAC

    # ── Autenticación del app móvil ──────────────────────────────────────────
    ("POST", "/api/mobile/auth/login"),
    ("POST", "/api/mobile/auth/refresh"),  # el refresh viaja en el body

    # ── Alta pública de solicitudes de registro ──────────────────────────────
    ("POST", "/api/medicos/register"),
    # TODO(seguridad): este endpoint acepta cualquier `medico_id`. Debería exigir
    # un token de un solo uso emitido por POST /register. Ver B1 en
    # docs/api/AUDITORIA_SEGURIDAD.md.
    ("POST", "/api/medicos/register/{medico_id}/document"),

    # ── Portal público ───────────────────────────────────────────────────────
    # Agregadas el 2026-08-05: el default cerrado de B1 las había dejado en 401 y
    # eso rompió el sitio para los visitantes anónimos. Son las que alimentan la
    # home del Colegio.
    #
    # Tres de las cinco **no devuelven lo mismo a un anónimo que a un usuario
    # autenticado**, y esa diferencia la impone el handler, no esta lista:
    #
    #   * `/noticias/` y `/noticias/{id}` ocultan las **no publicadas**, o sea
    #     los borradores del editor;
    #   * `/publicidad-medicos/` oculta los avisos **inactivos**;
    #   * `/obras_social/` recorta CUIT, contactos, condiciones del convenio y
    #     la lista de documentos, que son datos comerciales.
    #
    # El patrón es `usuario_opcional()` de app/auth/deps.py. Que la ruta esté
    # acá significa "no exige token", NO significa "devuelve todo".
    ("GET", "/api/noticias/"),
    ("GET", "/api/noticias/{id}"),
    ("GET", "/api/noticias/{id}/documentos"),
    ("GET", "/api/publicidad-medicos/"),
    ("GET", "/api/obras_social/"),
}


def is_public(method: str, route_path: str) -> bool:
    return (method.upper(), route_path) in PUBLIC_ROUTES
