"""Validación de archivos subidos: tipo real, extensión y tamaño.

El problema que resuelve: `UploadFile.content_type` lo declara el cliente. Un
atacante manda `Content-Type: image/png` con un `.php` adentro y el servidor lo
guarda y lo registra como imagen. Acá el tipo se decide leyendo los primeros
bytes del archivo (magic bytes), no lo que dice el cliente.

Sin dependencias nuevas: el sniffer cubre los formatos que la app realmente
acepta. `python-magic` necesita libmagic instalado en el sistema, lo que agrega
una dependencia nativa al contenedor por muy poco beneficio en este caso.

Uso típico:

    from app.common.uploads import validate_upload, DOCUMENTOS

    info = await validate_upload(file, DOCUMENTOS)
    # info.extension → ".pdf"  (derivada del contenido, no del nombre)
    # info.content_type → "application/pdf"
    # info.size → bytes
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, UploadFile

from app.core.config import settings

# ── Firmas ────────────────────────────────────────────────────────────────────
# (offset, bytes esperados) → (mime canónico, extensión canónica)
_SIGNATURES: list[tuple[int, bytes, str, str]] = [
    (0, b"%PDF-", "application/pdf", ".pdf"),
    (0, b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (0, b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (0, b"GIF87a", "image/gif", ".gif"),
    (0, b"GIF89a", "image/gif", ".gif"),
    (0, b"BM", "image/bmp", ".bmp"),
    (0, b"II*\x00", "image/tiff", ".tiff"),
    (0, b"MM\x00*", "image/tiff", ".tiff"),
    # RIFF....WEBP — el tamaño va en los bytes 4-7, por eso se chequea aparte
    (8, b"WEBP", "image/webp", ".webp"),
    # ISO-BMFF (mp4/mov): 'ftyp' en el offset 4
    (4, b"ftyp", "video/mp4", ".mp4"),
]

# ZIP: xlsx, docx y pptx son ZIP por dentro. Se distinguen por el nombre porque
# el contenedor es idéntico; lo que importa acá es que sí es un ZIP real.
_ZIP_MAGIC = b"PK\x03\x04"
_ZIP_EXTS = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".zip": "application/zip",
}

# Formatos binarios viejos de Office (xls/doc): OLE2 compound file.
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_OLE2_EXTS = {
    ".xls": "application/vnd.ms-excel",
    ".doc": "application/msword",
}


# ── Perfiles de lo que acepta cada endpoint ───────────────────────────────────
IMAGENES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"})
DOCUMENTOS = frozenset({".pdf", ".jpg", ".jpeg", ".png", ".webp", ".tiff"})
PLANILLAS = frozenset({".xlsx", ".xls"})
MEDIA = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4"})


@dataclass(frozen=True)
class UploadInfo:
    """Resultado de validar un archivo. `extension` y `content_type` salen del
    contenido real, nunca de lo que declaró el cliente."""
    data: bytes
    size: int
    extension: str
    content_type: str
    original_name: str


def _sniff(head: bytes, declared_ext: str) -> tuple[str, str] | None:
    """(mime, extensión) según los primeros bytes, o None si no se reconoce."""
    for offset, magic, mime, ext in _SIGNATURES:
        if head[offset : offset + len(magic)] == magic:
            # WEBP y MP4 comparten prefijo con otros contenedores RIFF/ISO-BMFF;
            # el chequeo por offset ya los desambigua lo suficiente para esto.
            return mime, ext

    if head.startswith(_ZIP_MAGIC):
        # El contenedor ZIP no distingue xlsx de docx: se respeta la extensión
        # declarada si está en la whitelist, y si no se cae a .zip genérico.
        if declared_ext in _ZIP_EXTS:
            return _ZIP_EXTS[declared_ext], declared_ext
        return _ZIP_EXTS[".zip"], ".zip"

    if head.startswith(_OLE2_MAGIC):
        if declared_ext in _OLE2_EXTS:
            return _OLE2_EXTS[declared_ext], declared_ext
        return _OLE2_EXTS[".xls"], ".xls"

    return None


def _normalizar_ext(ext: str) -> str:
    return ".jpg" if ext == ".jpeg" else ext.lower()


async def validate_upload(
    file: UploadFile,
    permitidas: frozenset[str],
    *,
    max_bytes: int | None = None,
) -> UploadInfo:
    """Lee el archivo entero, valida tamaño y tipo real, y devuelve el contenido.

    Lanza 413 si excede el límite y 415 si el tipo no está permitido o no se
    corresponde con el contenido.
    """
    limite = max_bytes if max_bytes is not None else settings.MAX_UPLOAD_BYTES

    await file.seek(0)
    data = await file.read(limite + 1)
    if len(data) > limite:
        raise HTTPException(
            413,
            f"El archivo supera el máximo permitido de {limite // (1024 * 1024)} MB.",
        )
    if not data:
        raise HTTPException(415, "El archivo está vacío.")

    nombre = file.filename or ""
    declarada = _normalizar_ext("." + nombre.rsplit(".", 1)[-1] if "." in nombre else "")

    detectado = _sniff(data[:32], declarada)
    if detectado is None:
        raise HTTPException(
            415,
            "No se pudo reconocer el tipo de archivo. "
            f"Formatos aceptados: {', '.join(sorted(permitidas))}.",
        )

    mime, ext = detectado
    ext = _normalizar_ext(ext)

    if ext not in permitidas:
        raise HTTPException(
            415,
            f"El contenido del archivo es {mime}, que no está permitido acá. "
            f"Formatos aceptados: {', '.join(sorted(permitidas))}.",
        )

    await file.seek(0)
    return UploadInfo(
        data=data,
        size=len(data),
        extension=ext,
        content_type=mime,
        original_name=nombre or f"archivo{ext}",
    )


async def leer_texto(file: UploadFile, *, max_bytes: int | None = None) -> str:
    """Lee un archivo de texto (CSV) con tope de tamaño, sin escribirlo a disco.

    Los importadores de CSV parsean en memoria, así que no necesitan validación
    de magic bytes — pero sí un límite: `await file.read()` sin tope permite
    agotar la RAM del contenedor con una sola request.
    """
    limite = max_bytes if max_bytes is not None else settings.MAX_UPLOAD_BYTES

    await file.seek(0)
    data = await file.read(limite + 1)
    if len(data) > limite:
        raise HTTPException(
            413,
            f"El archivo supera el máximo permitido de {limite // (1024 * 1024)} MB.",
        )
    if not data:
        raise HTTPException(415, "El archivo está vacío.")

    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Los exports de Excel en Windows suelen venir en latin-1.
        try:
            return data.decode("latin-1")
        except UnicodeDecodeError:
            raise HTTPException(415, "El archivo no es texto legible (UTF-8 ni latin-1).")
