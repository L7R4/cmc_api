"""Guardado de los PDF de planillas en `uploads/planillas/`.

Compartido por las rutas (alta y reemplazo de PDF) y por
`scripts/migrar_planillas_legacy.py`, que trae a este directorio los PDF que
hasta ahora servía la raíz del sitio legacy. Ver el docstring de `routes.py`
para el porqué del prefijo `planillas/` en `avisos.ARCHIVO`.
"""
import re
from pathlib import Path

from fastapi import HTTPException

#: Subdirectorio de `uploads/` y, a la vez, el prefijo que marca en `ARCHIVO`
#: que el PDF es nuestro y no del legacy.
SECCION = "planillas"
PLANILLAS_DIR = Path("uploads") / SECCION
PLANILLAS_DIR.mkdir(parents=True, exist_ok=True)

#: `avisos.ARCHIVO` es varchar(255) y hay que dejar lugar para `planillas/`.
MAX_NOMBRE = 255 - len(SECCION) - 1


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


def guardar_pdf(data: bytes, nombre_original: str) -> Path:
    """Escribe el PDF en `uploads/planillas/` con un nombre saneado y libre, y
    devuelve dónde quedó. Quien llama borra el archivo si después no persiste
    la fila que lo referencia."""
    destino = _destino_libre(_sanear(nombre_original))
    destino.write_bytes(data)
    return destino


def archivo_de(destino: Path) -> str:
    """Valor de `avisos.ARCHIVO` para un PDF guardado con `guardar_pdf`."""
    return f"{SECCION}/{destino.name}"


def destino_de(archivo: str) -> Path | None:
    """Ruta en disco de un `ARCHIVO` nuestro (con prefijo `planillas/`); None si
    es una planilla legacy."""
    if not archivo.startswith(f"{SECCION}/"):
        return None
    return PLANILLAS_DIR / archivo[len(SECCION) + 1:]
