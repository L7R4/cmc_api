from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from app.api.routes import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import setup_logging
from app.auth.router import router as auth_router
from app.auth.authz import enforce_authz
from app.middleware.audit import AuditMiddleware
from app.middleware.body_limit import BodyLimitMiddleware
from app.middleware.request_context import RequestContextMiddleware
from fastapi.middleware.cors import CORSMiddleware

import os
os.environ.setdefault("PASSLIB_BCRYPT_MINIMAL", "1")

# Antes de crear la app: así cualquier log de importación ya sale con el formato
# y el nivel definitivos.
setup_logging()

# En producción no se publica el esquema: /docs, /redoc y /openapi.json quedan
# en 404. Le entregaban a cualquiera el mapa exacto de la API con los schemas de
# request y response de cada endpoint.
_docs_kwargs = (
    {"openapi_url": None, "docs_url": None, "redoc_url": None}
    if settings.IS_PRODUCTION
    else {"openapi_url": "/openapi.json", "docs_url": "/docs", "redoc_url": "/redoc"}
)

app = FastAPI(
    title="CMC API",
    version="1.0",
    # Default cerrado en dos niveles:
    #   1. toda ruta exige access token salvo las de app/auth/public.py
    #   2. toda ruta exige el scope que declara app/auth/authz.py
    # Una ruta que no figure en ninguno de los dos archivos falla cerrada.
    dependencies=[Depends(enforce_authz)],
    **_docs_kwargs,
)

# Devuelven un `request_id` al cliente y mandan el traceback al log en vez de al
# response. Ver app/core/errors.py.
register_exception_handlers(app)

app.include_router(api_router, prefix="/api")
app.include_router(auth_router)  # expone /auth/*

# El orden importa: `add_middleware` inserta al principio, así que el ÚLTIMO
# agregado es el más externo. Queda:
#   RequestContext → BodyLimit → Audit → CORS → router
# RequestContext primero para que el request_id exista cuando Audit arma la fila
# y para que el header X-Request-ID salga en toda respuesta.
# BodyLimit inmediatamente después: cortar un body de 2 GB no tiene sentido si
# antes Audit ya lo leyó para guardar los primeros 4 KB en `audit_log`.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_LIST(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"],
    expose_headers=[
        "X-Total-Count", "Content-Range", "X-Offset", "X-Limit",
        # Sin esto el navegador no deja que el front lea el id para mostrarlo.
        "X-Request-ID", "Retry-After",
    ],
)
app.add_middleware(AuditMiddleware)
app.add_middleware(BodyLimitMiddleware)
app.add_middleware(RequestContextMiddleware)

# ── Adjuntos ────────────────────────────────────────────────────────────────
# Acá había un `app.mount("/uploads", StaticFiles(directory="uploads"))`, que
# publicaba el árbol entero —los 525 escaneos de DNI, títulos y constancias de
# CBU— sin ninguna autenticación.
#
# Y lo hacía de la peor manera: un `Mount` es un sub-ASGI-app, así que
# `dependencies=[Depends(enforce_authz)]` de arriba **no corre** sobre él. Como
# el Caddy de producción proxea todo `api.colegiomedicocorrientes.com` sin
# matcher de paths, `https://api.colegiomedicocorrientes.com/uploads/medicos/…`
# devolvía 200 a cualquiera. Era una segunda vía pública a los mismos archivos,
# además del `handle /uploads/*` del dominio principal que documentó S6.
#
# Ahora los adjuntos con dueño salen por `GET /api/archivos/{ruta}`, que sí pasa
# por la autorización global y resuelve la propiedad
# (`app/modules/archivos/routes.py`).
#
# Lo único que se sigue sirviendo estático son los dos directorios que son
# públicos por diseño: las imágenes de noticias y de publicidad las tiene que
# ver un visitante anónimo del portal. Van montados uno por uno, no por el
# padre, para que agregar un directorio nuevo bajo `uploads/` no lo publique sin
# que nadie lo haya decidido.
#
# En producción los sirve Caddy y estos mounts no se usan; existen para que el
# entorno de desarrollo se comporte igual.
for _publico in ("web_noticias", "medicos_publicidad"):
    _dir = os.path.join("uploads", _publico)
    os.makedirs(_dir, exist_ok=True)
    app.mount(f"/uploads/{_publico}", StaticFiles(directory=_dir), name=f"uploads_{_publico}")
