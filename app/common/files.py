import os
from typing import Optional, Tuple
from uuid import uuid4

from fastapi import UploadFile
from fastapi.concurrency import run_in_threadpool

from app.common.uploads import DOCUMENTOS, validate_upload
from app.db.models import Documento

UPLOAD_ROOT = "uploads"

# ── URL pública de un archivo guardado ───────────────────────────────────────
# Los adjuntos se guardan con rutas tipo `uploads/medicos/2514/abc.pdf`, y hasta
# ahora la API devolvía esa ruta tal cual: el front la usaba como URL relativa y
# el navegador la resolvía contra el dominio de la SPA, donde Caddy la servía
# **sin ninguna autenticación**. Ese era el hallazgo S6.
#
# `url_archivo()` es el único lugar donde se decide por dónde se pide cada
# archivo. Que sea uno solo es el punto: la alternativa —que cada módulo arme su
# URL— es cómo se llegó a tener dos convenciones distintas (unas con `/` inicial
# y otras sin él) y a que nadie supiera qué se servía público.
#
# La regla tiene dos ramas y la diferencia importa:
#
#   * `web_noticias/` y `medicos_publicidad/` son **contenido del portal**. Los
#     tiene que ver un visitante anónimo, así que siguen saliendo por `/uploads`
#     y los sigue sirviendo Caddy. Bloquearlos rompería la home.
#   * Todo lo demás —legajos, órdenes de pacientes, convenios, comprobantes—
#     sale por `/api/archivos/…`, que valida token y propiedad.
#     Ver app/modules/archivos/routes.py.
#
# Las URLs son **relativas al host de la API** (`/api/archivos/…`), no
# absolutas: el front ya concatena su `API_BASE` para todas las llamadas y así
# no hay que configurar el dominio en dos lugares. Las públicas quedan
# relativas al host del front, que es donde el navegador ya está parado.

PUBLICOS = ("web_noticias", "medicos_publicidad")


def url_archivo(path: Optional[str]) -> Optional[str]:
    """Ruta guardada → URL por la que el cliente tiene que pedir el archivo.

        uploads/medicos/2514/abc.pdf   → /api/archivos/medicos/2514/abc.pdf
        /uploads/web_noticias/x.jpg    → /uploads/web_noticias/x.jpg   (sin cambio)

    Tolera las dos convenciones que conviven en la base (con y sin `/` inicial,
    con y sin el prefijo `uploads/`) porque los módulos las guardaron distinto y
    reescribir 500 filas para uniformarlas es un riesgo que no hace falta correr.

    Un `path` vacío devuelve `None`: es lo que ya esperaba el front para "este
    médico no subió el título".
    """
    if not path:
        return None

    limpia = str(path).strip().replace("\\", "/").lstrip("/")
    if limpia.startswith("uploads/"):
        limpia = limpia[len("uploads/"):]
    if not limpia:
        return None

    # Una URL absoluta ya resuelta (las hay en `attach_*` de datos viejos) se
    # deja intacta: no es un archivo nuestro.
    if limpia.startswith("http://") or limpia.startswith("https://"):
        return str(path)

    seccion = limpia.split("/", 1)[0]
    if seccion in PUBLICOS:
        return f"/uploads/{limpia}"

    return f"/api/archivos/{limpia}"


async def save_upload_for_medico(medico_id: int, up: UploadFile) -> Optional[Tuple[Documento, str]]:
    """
    Guarda UploadFile en uploads/medicos/{id}/ y crea un Documento (sin commit).
    Devuelve (doc_model, rel_path) o None si no hay archivo.

    Valida tipo real y tamaño antes de escribir (ver app/common/uploads.py).
    """
    if not up or not up.filename:
        return None

    info = await validate_upload(up, DOCUMENTOS)

    dest_dir = os.path.join(UPLOAD_ROOT, "medicos", str(medico_id))
    os.makedirs(dest_dir, exist_ok=True)

    # Nombre generado, no derivado del que mandó el cliente: evita traversal
    # (`../`), colisiones y extensiones dobles del tipo `foto.png.php`. El
    # nombre original se conserva aparte, en `original_name`.
    filename = f"{uuid4().hex}{info.extension}"
    dest_path = os.path.join(dest_dir, filename)

    def _write_sync():
        with open(dest_path, "wb") as f:
            f.write(info.data)

    await run_in_threadpool(_write_sync)

    doc = Documento(
        medico_id=medico_id,
        label=None,
        original_name=os.path.basename(info.original_name),
        filename=filename,
        content_type=info.content_type,
        size=info.size,
        path=dest_path.replace("\\", "/"),
    )
    return doc, dest_path.replace("\\", "/")
