"""Factura de la refacturación dentro del lote.

Para el Colegio una refacturación genera una factura nueva de AFIP/ARCA. Acá se carga, dentro del lote
(`lote_ajuste.tipo = 'refacturacion'`), su tipo (A/B/C), su número (texto libre) y un único archivo
(PDF/JPG/PNG en `uploads/lotes/<id>/`). Todo es opcional y se puede corregir mientras el lote no esté
aplicado (estado AP = el pago ya se cerró).

No confundir con `nro_factura` de la respuesta del lote, que es el de la factura ORIGINAL que se corrige.
"""
import os
import re
import shutil
import uuid
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.files import UPLOAD_ROOT
from app.common.uploads import validate_upload
from app.db.database import get_db
from app.db.models import FacturacionCMC, LoteAjuste
from app.modules.lotes.schemas import LoteAjusteRead

router = APIRouter()

EXTENSIONES = frozenset({".pdf", ".jpg", ".png"})
MAX_ARCHIVO_BYTES = 10 * 1024 * 1024


class LoteFacturaRefactIn(BaseModel):
    """Tipo y número de la factura nueva. Los dos juntos o los dos en null (para dejarlos sin cargar)."""
    tipo_factura: Optional[Literal["A", "B", "C"]] = None
    nro_factura: Optional[str] = Field(None, max_length=20)


class LoteFacturaRefactOut(LoteAjusteRead):
    # Avisos que no bloquean (p. ej. el número ya figura en otra factura de la misma obra social).
    advertencias: List[str] = []


# ── Archivos ──────────────────────────────────────────────────────────────────
def _carpeta(lote_id: int) -> str:
    return os.path.join(UPLOAD_ROOT, "lotes", str(lote_id))


def borrar_archivo(path: Optional[str]) -> None:
    """Borra del disco un archivo ya sin fila. Nunca falla."""
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def borrar_carpeta_lote(lote_id: int) -> None:
    """Borra la carpeta de archivos del lote (al eliminarlo). Nunca falla."""
    shutil.rmtree(_carpeta(lote_id), ignore_errors=True)


def _escribir(lote_id: int, data: bytes, extension: str) -> str:
    destino_dir = _carpeta(lote_id)
    os.makedirs(destino_dir, exist_ok=True)
    destino = os.path.join(destino_dir, f"{uuid.uuid4().hex}{extension}").replace("\\", "/")
    with open(destino, "wb") as f:
        f.write(data)
    return destino


# ── Reglas ────────────────────────────────────────────────────────────────────
async def _lote_editable(db: AsyncSession, lote_id: int) -> LoteAjuste:
    lote = await db.get(LoteAjuste, lote_id)
    if not lote:
        raise HTTPException(404, "Lote no encontrado")
    if lote.tipo != "refacturacion":
        raise HTTPException(409, "Solo las refacturaciones llevan factura.")
    if lote.estado == "AP":
        raise HTTPException(409, "El lote ya está aplicado en un pago cerrado: no se puede cambiar su factura.")
    return lote


def _solo_digitos(texto: Optional[str]) -> str:
    return re.sub(r"\D", "", texto or "").lstrip("0")


async def _advertencias(db: AsyncSession, lote: LoteAjuste, nro: str) -> list[str]:
    """Avisos (nunca bloquean) si el número ya figura en otra refacturación o en una factura de la misma obra
    social. Se comparan sólo los dígitos: '00031-00000449' y '0003100000449' son el mismo número."""
    clave = _solo_digitos(nro)
    if not clave:
        return []
    avisos: list[str] = []

    otros = (await db.execute(
        select(LoteAjuste.id, LoteAjuste.refact_nro_factura).where(
            LoteAjuste.obra_social_id == lote.obra_social_id,
            LoteAjuste.tipo == "refacturacion",
            LoteAjuste.id != lote.id,
            LoteAjuste.refact_nro_factura.is_not(None),
        )
    )).all()
    ids = [i for i, n in otros if _solo_digitos(n) == clave]
    if ids:
        lista = ", ".join(f"#{i}" for i in ids[:5])
        avisos.append(f"El número {nro} ya está cargado en otra refacturación de esta obra social (lote {lista}).")

    facturas = (await db.execute(
        select(FacturacionCMC.id_prestaciones, FacturacionCMC.nro_factura, FacturacionCMC.periodo, FacturacionCMC.version)
        .where(FacturacionCMC.cod_obr == str(lote.obra_social_id), FacturacionCMC.nro_factura.is_not(None))
    )).all()
    for fid, n, periodo, version in facturas:
        if _solo_digitos(n) == clave:
            que = "la factura que se corrige" if fid == lote.factura_id else "una factura"
            avisos.append(
                f"El número {nro} ya figura en {que} de esta obra social "
                f"({n}, período {periodo}{'' if (version or 1) == 1 else f', complementaria {version - 1}'})."
            )
            break
    return avisos


async def _respuesta(db: AsyncSession, lote_id: int, advertencias: Optional[list[str]] = None) -> LoteFacturaRefactOut:
    # Import tardío: lotes/routes.py importa de este módulo.
    from app.modules.lotes import routes as lotes_routes

    lote = await lotes_routes._get_lote_with_ajustes(db, lote_id)
    rows = await lotes_routes._get_enriched_ajuste_rows(db, lote_id)
    base = await lotes_routes._respuesta_lote(db, lote, rows)
    return LoteFacturaRefactOut(**base.model_dump(), advertencias=advertencias or [])


# ── Endpoints ─────────────────────────────────────────────────────────────────
@router.patch("/snaps/{lote_id}/factura_refact", response_model=LoteFacturaRefactOut)
async def guardar_factura_refact(
    lote_id: int,
    payload: LoteFacturaRefactIn,
    db: AsyncSession = Depends(get_db),
):
    """Guarda (o limpia, con los dos en null) el tipo y el número de la factura de la refacturación.

    Se puede corregir con el lote abierto, cerrado o en el pago; aplicado (AP) no.
    """
    lote = await _lote_editable(db, lote_id)

    nro = (payload.nro_factura or "").strip() or None
    tipo = payload.tipo_factura
    if nro and not tipo:
        raise HTTPException(422, "Elegí el tipo de factura (A, B o C).")
    if tipo and not nro:
        raise HTTPException(422, "Cargá el número de la factura.")

    lote.refact_tipo_factura = tipo
    lote.refact_nro_factura = nro
    avisos = await _advertencias(db, lote, nro) if nro else []
    await db.commit()
    return await _respuesta(db, lote_id, avisos)


@router.put("/snaps/{lote_id}/factura_refact/documento", response_model=LoteFacturaRefactOut)
async def subir_documento_refact(
    lote_id: int,
    archivo: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Sube (o reemplaza) el único archivo de la factura: PDF, JPG o PNG (por su contenido real), hasta 10 MB."""
    lote = await _lote_editable(db, lote_id)
    info = await validate_upload(archivo, EXTENSIONES, max_bytes=MAX_ARCHIVO_BYTES)
    nuevo = await run_in_threadpool(_escribir, lote_id, info.data, info.extension)
    anterior = lote.refact_documento_path
    try:
        lote.refact_documento_path = nuevo
        lote.refact_documento_nombre = info.original_name[:255]
        await db.commit()
    except Exception:
        await db.rollback()
        borrar_archivo(nuevo)
        raise
    borrar_archivo(anterior)
    return await _respuesta(db, lote_id)


@router.delete("/snaps/{lote_id}/factura_refact/documento", response_model=LoteFacturaRefactOut)
async def quitar_documento_refact(lote_id: int, db: AsyncSession = Depends(get_db)):
    """Quita el archivo de la factura (el tipo y el número quedan)."""
    lote = await _lote_editable(db, lote_id)
    anterior = lote.refact_documento_path
    lote.refact_documento_path = None
    lote.refact_documento_nombre = None
    await db.commit()
    borrar_archivo(anterior)
    return await _respuesta(db, lote_id)
