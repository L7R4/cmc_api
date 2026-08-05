from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import String, cast, or_, select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.common.uploads import MEDIA, validate_upload
from app.db.database import get_db
from app.db.models import ListadoMedico, PublicidadMedico
from app.modules.contenido.schemas import PublicidadMedicoOut

router = APIRouter()

MEDICOS_ADS_DIR = Path("uploads/medicos_publicidad").resolve()
MEDICOS_ADS_DIR.mkdir(parents=True, exist_ok=True)


async def _save_file(file: UploadFile) -> dict:
    # Publicidad acepta imagen o video (MEDIA). Antes se guardaba cualquier
    # cosa con la extensión que viniera en el nombre.
    info = await validate_upload(file, MEDIA)
    name = f"{uuid4().hex}{info.extension}"
    (MEDICOS_ADS_DIR / name).write_bytes(info.data)
    return {
        "adjunto_filename": info.original_name,
        "adjunto_content_type": info.content_type,
        "adjunto_size": info.size,
        "adjunto_path": f"/uploads/medicos_publicidad/{name}",
    }


def _abs_from_path(p: str) -> Path:
    return (MEDICOS_ADS_DIR / Path(p).name).resolve()


def _row_to_out(row: PublicidadMedico, nombre: Optional[str]) -> PublicidadMedicoOut:
    return PublicidadMedicoOut(
        id=row.id,
        medico_id=row.medico_id,
        medico_nombre=nombre,
        activo=row.activo,
        adjunto_filename=row.adjunto_filename,
        adjunto_content_type=row.adjunto_content_type,
        adjunto_size=row.adjunto_size,
        adjunto_path=row.adjunto_path,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/", response_model=List[PublicidadMedicoOut])
async def listar_publicidades(
    q: Optional[str] = Query(None),
    activo: Optional[bool] = Query(None),
    medico_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    LM = aliased(ListadoMedico)
    stmt = select(PublicidadMedico, LM.NOMBRE).join(
        LM, LM.ID == PublicidadMedico.medico_id, isouter=True
    )

    if activo is not None:
        stmt = stmt.where(PublicidadMedico.activo.is_(activo))
    if medico_id is not None:
        stmt = stmt.where(PublicidadMedico.medico_id == medico_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(LM.NOMBRE.like(like))

    stmt = stmt.order_by(desc(PublicidadMedico.created_at))
    res = await db.execute(stmt)
    items = []
    for row, nombre in res.all():
        items.append(_row_to_out(row, nombre))
    return items


@router.get("/medicos/buscar", response_model=list[dict])
async def buscar_medicos(
    q: str,
    db: AsyncSession = Depends(get_db),
    limit: int = 20,
):
    term = f"%{q.strip()}%"
    LM = ListadoMedico

    stmt = (
        select(
            LM.ID.label("id"),
            LM.NOMBRE.label("nombre"),
            LM.NRO_SOCIO.label("nro_socio"),
            LM.MATRICULA_PROV.label("matricula_prov"),
            LM.MATRICULA_NAC.label("matricula_nac"),
            LM.DOCUMENTO.label("documento"),
        )
        .where(
            LM.EXISTE == "S",
            or_(
                LM.NOMBRE.like(term),
                cast(LM.NRO_SOCIO, String).like(term),
                LM.MATRICULA_PROV.like(term),
                LM.DOCUMENTO.like(term),
            ),
        )
        .order_by(LM.NOMBRE.asc())
        .limit(max(1, min(limit, 50)))
    )

    res = await db.execute(stmt)
    rows = res.mappings().all()

    return [
        {
            "id": r["id"],
            "nombre": r["nombre"],
            "nro_socio": r["nro_socio"],
            "matricula_prov": r["matricula_prov"],
            "matricula_nac": r["matricula_nac"],
            "documento": r["documento"],
        }
        for r in rows
    ]


@router.get("/{id}", response_model=PublicidadMedicoOut)
async def obtener_publicidad(id: int, db: AsyncSession = Depends(get_db)):
    LM = aliased(ListadoMedico)
    stmt = select(PublicidadMedico, LM.NOMBRE).join(
        LM, LM.ID == PublicidadMedico.medico_id, isouter=True
    ).where(PublicidadMedico.id == id)
    r = await db.execute(stmt)
    rec = r.first()
    if not rec:
        raise HTTPException(404, "Publicidad no encontrada")
    row, nombre = rec
    return _row_to_out(row, nombre)


@router.post("/", response_model=PublicidadMedicoOut)
async def crear_publicidad(
    medico_id: int = Form(...),
    activo: bool = Form(True),
    adjunto: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    meta = await _save_file(adjunto)
    pub = PublicidadMedico(
        medico_id=medico_id,
        activo=activo,
        **meta,
    )
    db.add(pub)
    await db.commit()
    await db.refresh(pub)

    nombre = None
    if pub.medico_id:
        r = await db.execute(select(ListadoMedico.NOMBRE).where(ListadoMedico.ID == pub.medico_id))
        nombre = r.scalar_one_or_none()
    return _row_to_out(pub, nombre)


@router.put("/{id}", response_model=PublicidadMedicoOut)
async def actualizar_publicidad(
    id: int,
    medico_id: Optional[int] = Form(None),
    activo: Optional[bool] = Form(None),
    adjunto: Optional[UploadFile] = File(None),
    limpiar_adjunto: Optional[bool] = Form(False),
    db: AsyncSession = Depends(get_db),
):
    pub = await db.get(PublicidadMedico, id)
    if not pub:
        raise HTTPException(404, "Publicidad no encontrada")

    if medico_id is not None:
        pub.medico_id = medico_id
    if activo is not None:
        pub.activo = activo

    if limpiar_adjunto:
        pub.adjunto_filename = None
        pub.adjunto_content_type = None
        pub.adjunto_size = None
        if pub.adjunto_path:
            try:
                _abs_from_path(pub.adjunto_path).unlink(missing_ok=True)
            except Exception:
                pass
        pub.adjunto_path = None

    if adjunto:
        if pub.adjunto_path:
            try:
                _abs_from_path(pub.adjunto_path).unlink(missing_ok=True)
            except Exception:
                pass
        meta = await _save_file(adjunto)
        pub.adjunto_filename = meta["adjunto_filename"]
        pub.adjunto_content_type = meta["adjunto_content_type"]
        pub.adjunto_size = meta["adjunto_size"]
        pub.adjunto_path = meta["adjunto_path"]

    await db.commit()
    await db.refresh(pub)

    nombre = None
    if pub.medico_id:
        r = await db.execute(select(ListadoMedico.NOMBRE).where(ListadoMedico.ID == pub.medico_id))
        nombre = r.scalar_one_or_none()
    return _row_to_out(pub, nombre)


@router.delete("/{id}")
async def eliminar_publicidad(id: int, db: AsyncSession = Depends(get_db)):
    pub = await db.get(PublicidadMedico, id)
    if not pub:
        raise HTTPException(404, "Publicidad no encontrada")

    if pub.adjunto_path:
        try:
            _abs_from_path(pub.adjunto_path).unlink(missing_ok=True)
        except Exception:
            pass

    await db.delete(pub)
    await db.commit()
    return {"ok": True, "deleted_id": id}
