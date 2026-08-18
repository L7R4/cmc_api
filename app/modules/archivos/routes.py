"""Entrega autorizada de adjuntos (S6 de la auditoría).

## El problema

Los 525 adjuntos de `uploads/` —escaneos de DNI frente y dorso, títulos,
matrículas, constancias de CBU— se servían por **dos** caminos, los dos sin
ninguna autenticación:

  1. `handle /uploads/*` en el Caddy de producción, con `file_server` directo
     sobre el volumen. Es el que documentó la auditoría.
  2. `app.mount("/uploads", StaticFiles(...))` en `app/main.py`. Este **no
     estaba documentado** y era peor: un `Mount` es un sub-ASGI-app, así que
     `dependencies=[Depends(enforce_authz)]` del `FastAPI(...)` **no corre**.
     Y como Caddy proxea todo `api.colegiomedicocorrientes.com` sin matcher de
     paths, `https://api.colegiomedicocorrientes.com/uploads/medicos/…` llegaba
     al mount y devolvía 200. Verificado con TestClient antes de sacarlo.

El renombrado a `uuid4` del 2026-08-04 hizo que las URLs no se pudieran
adivinar, y A4 cerró la reconstrucción del índice desde la API. Faltaba lo
único que es control de acceso de verdad: preguntar quién pide antes de
entregar el archivo.

## Por qué un solo endpoint con la ruta adentro

Se eligió `GET /api/archivos/{ruta:path}` en vez de un endpoint por recurso
(`/api/medicos/{id}/documentos/{doc_id}/archivo`) por dos motivos concretos:

  * **La base guarda rutas, no ids.** `documentos.path`,
    `listado_medico.attach_*` (once columnas) y
    `detalle_facturacion.orden_path` guardan todas un string tipo
    `uploads/medicos/2514/abc.pdf`. Un endpoint por recurso obligaría a
    resolver cada uno de esos orígenes por separado, y los `attach_*` ni
    siquiera tienen una fila propia que consultar.
  * **La migración del front es cambiar un prefijo.** De
    `https://colegiomedicocorrientes.com/uploads/…` a
    `https://api.colegiomedicocorrientes.com/api/archivos/…`, con el mismo
    resto de la ruta. Se acepta el path con o sin el `uploads/` inicial para
    que el front pueda concatenar tal cual lo que le devuelve la API.

## De dónde sale el dueño

**Del propio layout de directorios**, que ya codifica a quién pertenece cada
archivo:

| Subdirectorio | Segundo segmento | Helper | Scope administrativo |
|---|---|---|---|
| `medicos/` | `ListadoMedico.ID` | `medico_objetivo` | `medico:documento` |
| `validaciones/` | `NRO_SOCIO` | `socio_objetivo` | `medico:leer` |
| `obras_sociales/` | — | (sin dueño) | `catalogo:leer` |
| `boletin_valores_eticos/` | — | (sin dueño) | `catalogo:leer` |
| `facturas/` | id de factura | (sin dueño) | `facturacion:leer` **+** `medico:leer` |

Los dos identificadores **no son intercambiables** y por eso son dos helpers
distintos: `save_upload_for_medico()` escribe bajo la PK interna y
`adjuntar_orden()` bajo el número de socio. Confundirlos no fallaría
ruidosamente — compararía dos enteros que no representan lo mismo y devolvería
el archivo de otro médico. Ver `app/auth/ownership.py`.

`web_noticias/` y `medicos_publicidad/` **no pasan por acá**: son contenido del
portal, públicos por diseño, y los sigue sirviendo Caddy (y en desarrollo, un
`StaticFiles` acotado a esos dos directorios). Que estén fuera es deliberado:
un visitante anónimo tiene que poder ver las noticias.
"""
import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.auth.deps import get_current_user
from app.auth.ownership import medico_objetivo, socio_objetivo
from app.auth.scopes import Scope
from app.common.files import UPLOAD_ROOT

log = logging.getLogger(__name__)

router = APIRouter()

_RAIZ = Path(UPLOAD_ROOT).resolve()

# Directorios que este endpoint NO sirve, porque son públicos por diseño y los
# entrega Caddy. Listarlos explícitamente evita que alguien los "proteja" sin
# querer y rompa el portal.
PUBLICOS = frozenset({"web_noticias", "medicos_publicidad"})


def _resolver(ruta: str) -> Path:
    """Ruta absoluta dentro de `uploads/`, o 400/404.

    Es la mitad de seguridad que no tiene que ver con permisos: que
    `../../etc/passwd` o `../../../app/.env` no salgan de acá. Se resuelve el
    path real —siguiendo symlinks— y se exige que quede debajo de la raíz.
    `Path.resolve()` normaliza los `..` antes de la comparación, así que no
    alcanza con buscar la cadena "..": `medicos/2514/..%2f..%2f.env` ya viene
    decodificado por Starlette.
    """
    limpia = ruta.strip().lstrip("/")

    # El front concatena lo que le devuelve la API, y la base guarda las rutas
    # con el prefijo `uploads/`. Se acepta con y sin él.
    if limpia.startswith("uploads/"):
        limpia = limpia[len("uploads/"):]

    if not limpia:
        raise HTTPException(400, "Ruta vacía")

    # Los NUL rompen la comparación de paths en algunas libc.
    if "\x00" in limpia:
        raise HTTPException(400, "Ruta inválida")

    destino = (_RAIZ / limpia).resolve()

    if not destino.is_relative_to(_RAIZ):
        log.warning("intento de path traversal en /api/archivos: %r", ruta)
        raise HTTPException(400, "Ruta inválida")

    if not destino.is_file():
        raise HTTPException(404, "El archivo no existe")

    return destino


def _autorizar(relativa: Path, user: dict) -> None:
    """Aplica la regla de propiedad del subdirectorio. Lanza 403 si no da.

    Falla cerrado: un subdirectorio nuevo que nadie declaró acá no se sirve. Es
    lo mismo que hace `authz.py` con las rutas sin declarar, y por el mismo
    motivo — que agregar un tipo de adjunto no lo deje accesible por omisión.
    """
    partes = relativa.parts
    seccion = partes[0] if partes else ""

    if seccion in PUBLICOS:
        # No debería llegar acá (los sirve Caddy), pero si llega, no hay nada
        # que autorizar: son públicos.
        return

    if seccion == "medicos":
        # uploads/medicos/{ListadoMedico.ID}/archivo
        if len(partes) < 3:
            raise HTTPException(404, "El archivo no existe")
        try:
            dueño = int(partes[1])
        except ValueError:
            raise HTTPException(404, "El archivo no existe")
        # MEDICO_DOCUMENTO y no MEDICO_LEER: son escaneos de DNI y constancias
        # de CBU. Es el mismo scope que exige el índice en
        # GET /api/medicos/{id}/documentos, así que quien puede ver la lista
        # puede ver los archivos, y nadie más.
        medico_objetivo(user, dueño, scope_admin=Scope.MEDICO_DOCUMENTO)
        return

    if seccion == "validaciones":
        # uploads/validaciones/{NRO_SOCIO}/archivo — órdenes y recetas de
        # pacientes que el prestador adjunta a una prestación.
        #
        # El dueño es el médico que la cargó, y además la ve el personal del
        # Colegio con MEDICO_LEER: son ellos quienes auditan la prestación
        # contra la orden cuando la obra social la debita.
        if len(partes) < 3:
            raise HTTPException(404, "El archivo no existe")
        try:
            dueño = int(partes[1])
        except ValueError:
            raise HTTPException(404, "El archivo no existe")
        socio_objetivo(user, dueño, scope_admin=Scope.MEDICO_LEER)
        return

    # ── Sin dueño individual: se resuelven por scope ─────────────────────────
    scopes = user.get("scopes") or []

    if seccion in ("obras_sociales", "boletin_valores_eticos"):
        # Convenios, anexos y el boletín de valores éticos. No tienen un médico
        # dueño: es material del catálogo, publicado para los colegiados, y lo
        # ve quien puede leer el catálogo — el mismo scope que exigen
        # `GET /api/obras_social/{id}` y `GET /api/valores-eticos/`.
        if Scope.CATALOGO_LEER not in scopes:
            raise HTTPException(403, f"Falta el permiso '{Scope.CATALOGO_LEER}'")
        return

    if seccion == "planillas":
        # uploads/planillas/<nombre>.pdf — las planillas de consulta que el
        # Colegio publica para todos los colegiados. No tienen dueño: son
        # material publicado, y lo ve quien puede leer contenido, el mismo
        # scope que exige `GET /api/planillas/`.
        #
        # No van en PUBLICOS: el legacy las servía sin login desde la raíz del
        # sitio, pero eso era una consecuencia de dónde estaba el archivo, no
        # una decisión. Acá quedan detrás del token como el resto del portal.
        if Scope.CONTENIDO_LEER not in scopes:
            raise HTTPException(403, f"Falta el permiso '{Scope.CONTENIDO_LEER}'")
        return

    if seccion == "facturas":
        # uploads/facturas/{FacturacionCMC.id_prestaciones}/comprobante
        #
        # Son los comprobantes de las facturas que el Colegio le emite a cada
        # obra social: la clave es `cod_obr` + `periodo`, no hay un médico
        # dueño. Por eso no se resuelve por propiedad sino por scope.
        #
        # Y por eso hacen falta LOS DOS: `facturacion:leer` solo no alcanza,
        # porque el rol `medico` también lo tiene —significa "ver mi propia
        # facturación"— y esto no es de nadie en particular. `medico:leer` es
        # la llave administrativa que `ownership.py` ya usa para distinguir al
        # personal del Colegio del colegiado. La intersección da exactamente
        # `facturador`, `liquidador` y `admin`.
        if Scope.FACTURACION_LEER not in scopes or Scope.MEDICO_LEER not in scopes:
            raise HTTPException(
                403,
                f"Faltan los permisos '{Scope.FACTURACION_LEER}' y '{Scope.MEDICO_LEER}'",
            )
        return

    log.error(
        "subdirectorio de uploads sin regla de autorización: %r — agregalo a "
        "app/modules/archivos/routes.py::_autorizar",
        seccion,
    )
    raise HTTPException(403, "Este tipo de adjunto no se puede descargar")


@router.get("/{ruta:path}")
async def descargar(ruta: str, user: dict = Depends(get_current_user)):
    """Entrega un adjunto, si quien lo pide tiene derecho a verlo.

    El orden importa: primero se resuelve la ruta (traversal), después se
    autoriza, y recién al final se lee del disco.

    Los 403 y 404 se emiten sin distinguir "no existe" de "no es tuyo" más de
    lo necesario: un `medico_id` ajeno da 403 —el helper de propiedad ya lo
    dice— pero un archivo inexistente dentro del propio directorio da 404 sin
    filtrar nada.
    """
    destino = _resolver(ruta)
    relativa = destino.relative_to(_RAIZ)

    _autorizar(relativa, user)

    tipo, _ = mimetypes.guess_type(destino.name)
    return FileResponse(
        destino,
        media_type=tipo or "application/octet-stream",
        # Sin `filename=`: iría como `Content-Disposition: attachment` y forzaría
        # la descarga. El front los muestra embebidos (previsualización de un DNI
        # o un título), así que se dejan inline.
        headers={
            # Son datos personales: que ningún proxy intermedio los guarde.
            "Cache-Control": "private, max-age=0, no-store",
        },
    )
