"""Planillas de consulta que el Colegio publica para los médicos.

Reemplaza `planilla_consulta_dres.php` (lectura del médico) y
`planilla_consulta_colegio.php` (alta y baja del Colegio) del legacy.

## Por qué esto vive en `avisos` y no en una tabla nueva

Porque las 15 planillas publicadas **ya están ahí** y el sitio legacy las sigue
listando desde la misma tabla mientras dure la convivencia. `avisos` guarda dos
cosas distintas discriminadas por `AVISO_PLANILLA`:

    'A' → avisos del portal legacy   (no los toca este módulo)
    'P' → planillas de consulta      (esto)

`EXISTE` es la baja lógica ('S' / 'N'), igual que en el resto del legacy:
`borrar_avisos.php` nunca borró una fila.

## Dónde está el PDF

Dos orígenes, y la diferencia se lee en `ARCHIVO`:

  * **Las históricas** guardan el nombre pelado (`PlanillaIOSCOR.pdf`) y el
    archivo vive en la raíz del sitio legacy. No se migran: son 15 PDFs que el
    legacy sigue sirviendo y moverlos rompería su propia pantalla.
  * **Las que se suben desde acá** guardan `planillas/<nombre>.pdf` y el archivo
    va a `uploads/planillas/`, servido por `GET /api/archivos/planillas/…`.

El `/` es el discriminador, no una heurística de "existe en disco": una consulta
no tiene que depender de que el volumen esté montado para decidir a dónde
apuntar el link. `ARCHIVO` es `varchar(50)`, así que el nombre se recorta a
`MAX_NOMBRE` para que entre junto con el prefijo.
"""
import datetime
import re
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path as PathParam, Query, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.uploads import validate_upload
from app.core.config import settings
from app.db.database import get_db
from app.db.models import Avisos
from app.modules.planillas.schemas import PlanillaOut

router = APIRouter()  # El scope lo declara app/auth/authz.py::SCOPES_POR_RUTA (fuente unica de autorizacion).

#: Subdirectorio de `uploads/` y, a la vez, el prefijo que marca en `ARCHIVO`
#: que el PDF es nuestro y no del legacy.
SECCION = "planillas"
PLANILLAS_DIR = Path("uploads") / SECCION
PLANILLAS_DIR.mkdir(parents=True, exist_ok=True)

#: `avisos.ARCHIVO` es varchar(50) y hay que dejar lugar para `planillas/`.
MAX_NOMBRE = 50 - len(SECCION) - 1

#: Sólo PDF: es lo único que el Colegio publica acá y lo único que el visor del
#: front sabe abrir embebido.
EXTENSIONES = frozenset({".pdf"})


def _url(archivo: str) -> Optional[str]:
    """Por dónde pedir el PDF de esta fila. Ver el docstring del módulo."""
    if archivo.startswith(f"{SECCION}/"):
        # El nombre conserva espacios, así que se codifica igual que la URL
        # legacy: una URL cruda con espacios no es una URL.
        return f"/api/archivos/{SECCION}/{quote(archivo[len(SECCION) + 1:])}"
    base = (settings.LEGACY_BASE_URL or "").rstrip("/")
    return f"{base}/{quote(archivo)}" if base else None


def _to_out(row: Avisos) -> PlanillaOut:
    archivo = row.ARCHIVO or ""
    return PlanillaOut(
        id=row.ID,
        descripcion=(row.AVISO or "").strip() or archivo.rsplit("/", 1)[-1],
        archivo=archivo.rsplit("/", 1)[-1],
        fecha=row.FECHA or "",
        url=_url(archivo),
    )


def _sanear(nombre: str) -> str:
    """Nombre de archivo seguro y corto, derivado del que subió el usuario.

    Se conserva el nombre original (recortado) en vez de un uuid porque acá es
    contenido público que el médico identifica por cómo se llama —«Planilla
    IOSCOR»—, no un adjunto personal que haya que volver inadivinable.
    """
    base = Path(nombre or "").name
    tallo = base[: -len(".pdf")] if base.lower().endswith(".pdf") else base
    # Todo lo que no sea alfanumérico ASCII, espacio, guión o punto se cae:
    # así no hay separadores de ruta, ni acentos que rompan la URL, ni NUL.
    tallo = re.sub(r"[^A-Za-z0-9 ._-]", "_", tallo).strip(" ._-")
    tallo = re.sub(r"_{2,}", "_", tallo) or "planilla"
    return f"{tallo[: MAX_NOMBRE - len('.pdf')]}.pdf"


def _destino_libre(nombre: str) -> Path:
    """Ruta en disco que todavía no existe, agregando `-2`, `-3`… si hace falta.

    Sin esto, subir dos veces «Planilla IOSCOR.pdf» pisaría el PDF de la fila
    anterior, que sigue publicada y apuntando al mismo nombre.
    """
    destino = PLANILLAS_DIR / nombre
    if not destino.exists():
        return destino

    tallo = nombre[: -len(".pdf")]
    for n in range(2, 1000):
        sufijo = f"-{n}"
        recortado = tallo[: MAX_NOMBRE - len(".pdf") - len(sufijo)]
        candidato = PLANILLAS_DIR / f"{recortado}{sufijo}.pdf"
        if not candidato.exists():
            return candidato
    raise HTTPException(409, "Demasiadas planillas con ese nombre; renombrá el archivo.")


@router.get("/", response_model=List[PlanillaOut])
async def listar_planillas(
    q: Optional[str] = Query(None, max_length=120, description="Busca en descripción y nombre de archivo"),
    db: AsyncSession = Depends(get_db),
):
    """Las planillas publicadas, la más reciente primero.

    Ordena por `ID DESC` —igual que el legacy— y no por `FECHA`: la columna es
    un `varchar(10)` con dos formatos conviviendo (`2026-05-16` y `11/02/2026`),
    así que ordenar por ella en SQL da un resultado sin sentido. El front las
    reordena por fecha real con `ordenarPlanillas()`.
    """
    stmt = (
        select(Avisos)
        .where(Avisos.AVISO_PLANILLA == "P", Avisos.EXISTE == "S")
        .order_by(Avisos.ID.desc())
    )
    filas = (await db.execute(stmt)).scalars().all()

    salida = [_to_out(f) for f in filas]
    if q:
        aguja = q.strip().lower()
        salida = [
            p for p in salida
            if aguja in p.descripcion.lower() or aguja in p.archivo.lower()
        ]
    return salida


@router.post("/", response_model=PlanillaOut, status_code=status.HTTP_201_CREATED)
async def crear_planilla(
    archivo: UploadFile = File(..., description="El PDF de la planilla"),
    descripcion: Optional[str] = Form(None, max_length=500),
    fecha: Optional[str] = Form(None, max_length=10, description="YYYY-MM-DD; por defecto, hoy"),
    db: AsyncSession = Depends(get_db),
):
    """Publica una planilla. El PDF queda en `uploads/planillas/`.

    Sin `descripcion` se usa el nombre del archivo, que es lo que hacía el
    legacy cuando el campo venía vacío.
    """
    # El tipo sale de los magic bytes: `.pdf` en el nombre no prueba nada.
    info = await validate_upload(archivo, EXTENSIONES)

    destino = _destino_libre(_sanear(info.original_name))
    destino.write_bytes(info.data)

    fila = Avisos(
        AVISO=(descripcion or "").strip() or destino.name,
        ARCHIVO=f"{SECCION}/{destino.name}",
        FECHA=(fecha or "").strip() or datetime.date.today().isoformat(),
        EXISTE="S",
        AVISO_PLANILLA="P",
    )
    db.add(fila)
    try:
        await db.commit()
    except Exception:
        # Si la fila no entra, el PDF huérfano en disco sólo sería basura que
        # nadie referencia. Se borra acá porque es el único momento en que se
        # sabe que quedó suelto.
        destino.unlink(missing_ok=True)
        raise
    await db.refresh(fila)
    return _to_out(fila)


@router.delete("/{planilla_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_planilla(
    planilla_id: int = PathParam(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Baja lógica: `EXISTE='N'`, igual que `borrar_avisos.php`.

    El PDF **no** se borra del disco. La fila se puede reactivar a mano y el
    sitio legacy podría seguir enlazándola; borrar el archivo convertiría una
    baja reversible en una pérdida de datos.
    """
    fila = await db.get(Avisos, planilla_id)
    if not fila or fila.AVISO_PLANILLA != "P" or fila.EXISTE != "S":
        raise HTTPException(404, "Planilla no encontrada")

    fila.EXISTE = "N"
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
