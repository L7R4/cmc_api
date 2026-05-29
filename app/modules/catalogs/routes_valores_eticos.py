from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.database import get_db
from app.db.models import ValoresEticos
from app.modules.catalogs.schemas import ValoresEticosOut

router = APIRouter()

VALORES_ETICOS_DIR = Path("uploads") / "boletin_valores_eticos"
VALORES_ETICOS_DIR.mkdir(parents=True, exist_ok=True)


async def _save_pdf(file: UploadFile) -> str:
    ext = Path(file.filename or "").suffix.lower() or ".pdf"
    filename = f"{uuid4().hex}{ext}"
    dest = VALORES_ETICOS_DIR / filename
    dest.write_bytes(await file.read())
    return f"uploads/boletin_valores_eticos/{filename}"


@router.get(
    "/",
    response_model=List[ValoresEticosOut],
    summary="Listar todos los documentos de valores éticos (más reciente primero)",
)
async def listar_valores_eticos(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    result = await db.execute(select(ValoresEticos).order_by(desc(ValoresEticos.fecha_update)))
    return result.scalars().all()


@router.get(
    "/ultimo",
    response_model=ValoresEticosOut,
    summary="Obtener el documento de valores éticos más reciente",
)
async def ultimo_valor_etico(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    result = await db.execute(
        select(ValoresEticos).order_by(desc(ValoresEticos.fecha_update)).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="No hay documentos cargados")
    return row


@router.post(
    "/",
    response_model=ValoresEticosOut,
    status_code=status.HTTP_201_CREATED,
    summary="Subir nuevo documento de valores éticos (agrega al historial)",
)
async def crear_valor_etico(
    file: UploadFile = File(...),
    observaciones: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=422, detail="Solo se aceptan archivos PDF")

    pdf_path = await _save_pdf(file)
    row = ValoresEticos(
        pdf_path=pdf_path,
        observaciones=observaciones,
        fecha_update=datetime.now(timezone.utc),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete(
    "/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar un documento del historial (también borra el archivo)",
)
async def eliminar_valor_etico(
    id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    row = await db.get(ValoresEticos, id)
    if row is None:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    if row.pdf_path:
        Path(row.pdf_path).unlink(missing_ok=True)

    await db.delete(row)
    await db.commit()
