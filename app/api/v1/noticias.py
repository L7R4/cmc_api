from fastapi import APIRouter, Depends, HTTPException, Query, status, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, delete
from typing import List, Literal, Optional
from uuid import uuid4
from pathlib import Path

from sqlalchemy.orm import selectinload

from app.db.database import get_db
from app.db.models import Noticia as NoticiaModel, DocumentoNoticias as DocNoticiaModel
from app.schemas.noticias_schema import NoticiaOut, NoticiaDetailOut, DocumentoNoticiasOut
from app.auth.deps import get_current_user

router = APIRouter()

WEB_NEWS_DIR = Path("uploads") / "web_noticias"
WEB_NEWS_DIR.mkdir(parents=True, exist_ok=True)

#region HELPERS
def _is_image(content_type: str | None) -> bool:
    return bool(content_type and content_type.lower().startswith("image/"))

async def _save_file(file: UploadFile) -> dict:
    ext = Path(file.filename or "").suffix.lower() or ".bin"
    name = f"{uuid4().hex}{ext}"
    dest = WEB_NEWS_DIR / name
    data = await file.read()
    dest.write_bytes(data)
    return {
        "original_name": file.filename or name,
        "filename": name,
        "content_type": file.content_type,
        "size": len(data),
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
        portada=row.portada,    # ✅
        documentos=[_to_doc_out(d) for d in (docs or row.documentos or [])],
    )

def _abs_from_doc_path(p: str) -> Path:
    """
    Convierte '/uploads/web_noticias/<file>' => WEB_NEWS_DIR/<file>
    Evita path traversal usando solo el nombre del archivo.
    """
    name = Path(p).name
    return (WEB_NEWS_DIR / name).resolve()

async def _try_unlink_file(doc_path: str) -> None:
    try:
        fp = _abs_from_doc_path(doc_path)
        if fp.is_file():
            fp.unlink(missing_ok=True)
    except Exception:
        # Podés loggear si querés, pero no rompas el flujo de borrado
        pass
    
#endregion


# LISTAR NOTICIAS ==========================================
@router.get("/", response_model=List[NoticiaOut])
async def list_noticias(
    tipo: Optional[Literal["Blog", "Noticia"]] = Query(None, description="Filtrar por tipo"),
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
            tipo = n.tipo,
            fecha_creacion=n.fecha_creacion,
            fecha_actualizacion=n.fecha_actualizacion,
            portada=n.portada, 
        )
        for n in res.scalars().all()
    ]


# OBTENER UNA NOTICIA [ID] ==========================================
@router.get("/{id}", response_model=NoticiaDetailOut)
async def obtener_noticia(id: int, db: AsyncSession = Depends(get_db)):
    n = await db.get(NoticiaModel, id)
    if not n:
        raise HTTPException(status_code=404, detail="Noticia no encontrada")
    await db.refresh(n, attribute_names=["documentos"])
    return _to_out(n)


# CREAR NOTICIA ==========================================
@router.post("/", response_model=NoticiaDetailOut)
async def crear_noticia(
    titulo: str = Form(...),
    resumen: str = Form(...),
    contenido: str = Form(...),
    tipo: str = Form(...),
    publicada: bool = Form(True),
    autor: Optional[str] = Form(None),

    # ✅ NUEVO: portada opcional (single)
    portada: Optional[UploadFile] = File(None),

    # adjuntos múltiples (contenido)
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
    )

    # guardar portada
    if portada:
        meta = await _save_file(portada)
        n.portada = meta["path"]

    db.add(n)
    await db.flush()  # para n.id

    # guardar adjuntos
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


# ACTUALIZAR NOTICIA ==========================================
@router.put("/{id}", response_model=NoticiaDetailOut)
async def actualizar_noticia(
    id: int,
    titulo: Optional[str] = Form(None),
    resumen: Optional[str] = Form(None),
    contenido: Optional[str] = Form(None),
    tipo: Optional[str] = Form(None),
    publicada: Optional[bool] = Form(None),
    autor: Optional[str] = Form(None),

    # ✅ portada nueva (reemplaza) o limpiar
    portada: Optional[UploadFile] = File(None),
    limpiar_portada: Optional[bool] = Form(False),

    # adjuntos nuevos
    adjuntos: Optional[List[UploadFile]] = File(None),

    # borrar docs: "1,2,3"
    eliminar_documento_ids: Optional[str] = Form(None),

    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    n = await db.get(NoticiaModel, id, options=[selectinload(NoticiaModel.documentos)])
    if not n:
        raise HTTPException(status_code=404, detail="Noticia no encontrada")

    if titulo is not None: n.titulo = titulo.strip()
    if resumen is not None: n.resumen = resumen.strip()
    if contenido is not None: n.contenido = contenido
    if publicada is not None: n.publicada = publicada
    if autor is not None: n.autor = autor.strip() or n.autor

    # portada
    if limpiar_portada:
        n.portada = None
    if portada:
        meta = await _save_file(portada)
        n.portada = meta["path"]

    # adjuntos nuevos
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

    # eliminar docs específicos
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


# ELIMINAR UNA NOTICIA ==========================================
@router.delete("/{id}")
async def eliminar_noticia(id: int, db: AsyncSession = Depends(get_db)):
    # Traemos la noticia con sus documentos para borrar archivos físicos antes del delete
    result = await db.execute(
        select(NoticiaModel)
        .options(selectinload(NoticiaModel.documentos))
        .where(NoticiaModel.id == id)
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Noticia no encontrada")

    # Borramos archivos físicos (si existen)
    for doc in list(row.documentos or []):
        if doc.path:
            await _try_unlink_file(doc.path)

    # Borramos la noticia; los hijos se borran por cascade (DB o SQLAlchemy)
    await db.delete(row)
    await db.commit()

    return {"ok": True, "deleted_id": id}


# OBTENER UN DOCUMENTO DE NOTICIAS ==========================================
@router.get("/{id}/documentos", response_model=List[DocumentoNoticiasOut])
async def listar_documentos_noticia(id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(DocNoticiaModel).where(DocNoticiaModel.noticia_id == id)
    )
    docs = result.scalars().all()
    return [_to_doc_out(d) for d in docs]


# ELIMINAR UN DOCUMENTO DE UNA NOTICIA ==========================================
@router.delete("/{id}/documentos/{doc_id}")
async def eliminar_documento_noticia(
    id: int, doc_id: int, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    row = await db.get(NoticiaModel, id)
    if not row:
        raise HTTPException(status_code=404, detail="Noticia no encontrada")

    doc = await db.get(DocNoticiaModel, doc_id)
    if not doc or doc.noticia_id != row.id:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    await db.delete(doc)
    await db.commit()
    return {"mensaje": "Documento eliminado"}


# ELIMINAR UN DOCUMENTO DE UNA NOTICIA ==========================================
@router.delete("/{noticia_id}/documentos/{doc_id}")
async def eliminar_documento_noticia(noticia_id: int, doc_id: int, db: AsyncSession = Depends(get_db)):
    doc = await db.get(DocNoticiaModel, doc_id)
    if not doc or doc.noticia_id != noticia_id:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    # (opcional) borrar archivo físico del disco
    if doc.path:
        try:
            fp = _abs_from_doc_path(doc.path)  # misma helper que en el DELETE de noticia
            if fp.is_file():
                fp.unlink(missing_ok=True)
        except Exception:
            pass
    await db.delete(doc)
    await db.commit()
    return {"ok": True}


