from pathlib import Path
from typing import List, Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.deps import get_current_user
from app.common.uploads import IMAGENES, validate_upload
from app.db.database import get_db
from app.db.models import DocumentoNoticias as DocNoticiaModel
from app.db.models import Noticia as NoticiaModel
from app.modules.contenido.schemas import DocumentoNoticiasOut, NoticiaDetailOut, NoticiaOut

router = APIRouter()

WEB_NEWS_DIR = Path("uploads") / "web_noticias"
WEB_NEWS_DIR.mkdir(parents=True, exist_ok=True)


# region Helpers
def _is_image(content_type: str | None) -> bool:
    return bool(content_type and content_type.lower().startswith("image/"))


async def _save_file(file: UploadFile) -> dict:
    # El tipo sale de los magic bytes, no del content_type que declara el
    # cliente: `_is_image()` sobre el header era trivial de falsear.
    info = await validate_upload(file, IMAGENES)
    name = f"{uuid4().hex}{info.extension}"
    (WEB_NEWS_DIR / name).write_bytes(info.data)
    return {
        "original_name": info.original_name,
        "filename": name,
        "content_type": info.content_type,
        "size": info.size,
        "path": f"/uploads/web_noticias/{name}",
    }


def _to_doc_out(row: DocNoticiaModel) -> DocumentoNoticiasOut:
    return DocumentoNoticiasOut(
        id=row.id,
        label=row.label,
        original_name=row.original_name,
        filename=row.filename,
        content_type=row.content_type,
        size=row.size,
        path=row.path,
    )


def _to_out(row: NoticiaModel, docs: Optional[List[DocNoticiaModel]] = None) -> NoticiaDetailOut:
    return NoticiaDetailOut(
        id=str(row.id),
        titulo=row.titulo,
        contenido=row.contenido,
        resumen=row.resumen,
        autor=row.autor,
        publicada=row.publicada,
        fecha_creacion=row.fecha_creacion,
        fecha_actualizacion=row.fecha_actualizacion,
        portada=row.portada,
        badge=row.badge,
        documentos=[_to_doc_out(d) for d in (docs or row.documentos or [])],
    )


def _abs_from_doc_path(p: str) -> Path:
    name = Path(p).name
    return (WEB_NEWS_DIR / name).resolve()


async def _try_unlink_file(doc_path: str) -> None:
    try:
        fp = _abs_from_doc_path(doc_path)
        if fp.is_file():
            fp.unlink(missing_ok=True)
    except Exception:
        pass
# endregion


@router.get("/", response_model=List[NoticiaOut])
async def list_noticias(
    tipo: Optional[Literal["Blog", "Noticia", "Curso"]] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(NoticiaModel).where(NoticiaModel.publicada.is_(True))
    if tipo:
        stmt = stmt.where(NoticiaModel.tipo == tipo)
    stmt = stmt.order_by(desc(NoticiaModel.fecha_creacion))

    res = await db.execute(stmt)
    return [
        NoticiaOut(
            id=str(n.id),
            titulo=n.titulo,
            contenido=n.contenido,
            resumen=n.resumen,
            autor=n.autor,
            publicada=n.publicada,
            tipo=n.tipo,
            fecha_creacion=n.fecha_creacion,
            fecha_actualizacion=n.fecha_actualizacion,
            portada=n.portada,
            badge=n.badge,
        )
        for n in res.scalars().all()
    ]


@router.get("/{id}", response_model=NoticiaDetailOut)
async def obtener_noticia(id: int, db: AsyncSession = Depends(get_db)):
    n = await db.get(NoticiaModel, id)
    if not n:
        raise HTTPException(status_code=404, detail="Noticia no encontrada")
    await db.refresh(n, attribute_names=["documentos"])
    return _to_out(n)


@router.post("/", response_model=NoticiaDetailOut)
async def crear_noticia(
    titulo: str = Form(...),
    resumen: str = Form(...),
    contenido: str = Form(...),
    tipo: str = Form(...),
    publicada: bool = Form(True),
    autor: Optional[str] = Form(None),
    badge: Optional[str] = Form(None),
    portada: Optional[UploadFile] = File(None),
    adjuntos: Optional[List[UploadFile]] = File(None),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    n = NoticiaModel(
        titulo=titulo.strip(),
        resumen=resumen.strip(),
        contenido=contenido,
        tipo=tipo,
        publicada=publicada,
        autor=autor.strip() if autor else "Colegio Médico de Corrientes",
        badge=badge.strip() if badge else None,
    )

    if portada:
        meta = await _save_file(portada)
        n.portada = meta["path"]

    db.add(n)
    await db.flush()

    if adjuntos:
        for f in adjuntos:
            if not f:
                continue
            meta = await _save_file(f)
            doc = DocNoticiaModel(
                noticia_id=n.id,
                label="adjunto",
                original_name=meta["original_name"],
                filename=meta["filename"],
                content_type=meta["content_type"],
                size=meta["size"],
                path=meta["path"],
            )
            db.add(doc)

    await db.commit()
    await db.refresh(n)
    await db.refresh(n, attribute_names=["documentos"])
    return _to_out(n)


@router.put("/{id}", response_model=NoticiaDetailOut)
async def actualizar_noticia(
    id: int,
    titulo: Optional[str] = Form(None),
    resumen: Optional[str] = Form(None),
    contenido: Optional[str] = Form(None),
    tipo: Optional[str] = Form(None),
    publicada: Optional[bool] = Form(None),
    autor: Optional[str] = Form(None),
    portada: Optional[UploadFile] = File(None),
    badge: Optional[str] = Form(None),
    limpiar_portada: Optional[bool] = Form(False),
    adjuntos: Optional[List[UploadFile]] = File(None),
    eliminar_documento_ids: Optional[str] = Form(None),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    n = await db.get(NoticiaModel, id, options=[selectinload(NoticiaModel.documentos)])
    if not n:
        raise HTTPException(status_code=404, detail="Noticia no encontrada")

    if titulo is not None:
        n.titulo = titulo.strip()
    if resumen is not None:
        n.resumen = resumen.strip()
    if contenido is not None:
        n.contenido = contenido
    if publicada is not None:
        n.publicada = publicada
    if autor is not None:
        n.autor = autor.strip() or n.autor
    if badge is not None:
        n.badge = badge.strip() or None

    if limpiar_portada:
        n.portada = None
    if portada:
        meta = await _save_file(portada)
        n.portada = meta["path"]

    if adjuntos:
        for f in adjuntos:
            if not f:
                continue
            meta = await _save_file(f)
            db.add(DocNoticiaModel(
                noticia_id=n.id,
                label="adjunto",
                original_name=meta["original_name"],
                filename=meta["filename"],
                content_type=meta["content_type"],
                size=meta["size"],
                path=meta["path"],
            ))

    if eliminar_documento_ids:
        ids = [int(x) for x in eliminar_documento_ids.split(",") if x.strip().isdigit()]
        if ids:
            res = await db.execute(
                select(DocNoticiaModel).where(
                    DocNoticiaModel.noticia_id == n.id,
                    DocNoticiaModel.id.in_(ids),
                )
            )
            for d in res.scalars().all():
                await db.delete(d)

    await db.commit()
    await db.refresh(n)
    await db.refresh(n, attribute_names=["documentos"])
    return _to_out(n)


@router.delete("/{id}")
async def eliminar_noticia(id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(NoticiaModel)
        .options(selectinload(NoticiaModel.documentos))
        .where(NoticiaModel.id == id)
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Noticia no encontrada")

    for doc in list(row.documentos or []):
        if doc.path:
            await _try_unlink_file(doc.path)

    await db.delete(row)
    await db.commit()

    return {"ok": True, "deleted_id": id}


@router.get("/{id}/documentos", response_model=List[DocumentoNoticiasOut])
async def listar_documentos_noticia(id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(DocNoticiaModel).where(DocNoticiaModel.noticia_id == id)
    )
    docs = result.scalars().all()
    return [_to_doc_out(d) for d in docs]


@router.delete("/{noticia_id}/documentos/{doc_id}")
async def eliminar_documento_noticia(
    noticia_id: int,
    doc_id: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    doc = await db.get(DocNoticiaModel, doc_id)
    if not doc or doc.noticia_id != noticia_id:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    if doc.path:
        try:
            fp = _abs_from_doc_path(doc.path)
            if fp.is_file():
                fp.unlink(missing_ok=True)
        except Exception:
            pass
    await db.delete(doc)
    await db.commit()
    return {"ok": True}
