import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models.nomenclador_cmc import Convenio
from app.modules.nomenclador.schemas import (
    ConvenioCreate,
    ConvenioCerrarIn,
    ConvenioOut,
    ConvenioUpdate,
)

router = APIRouter()


@router.get("/", response_model=List[ConvenioOut])
async def list_convenios(
    obra_social_nro: Optional[int] = Query(None),
    estado: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Convenio)
    if obra_social_nro:
        stmt = stmt.where(Convenio.obra_social_nro == obra_social_nro)
    if estado:
        stmt = stmt.where(Convenio.estado == estado)
    stmt = stmt.order_by(Convenio.fecha_inicio.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/", response_model=ConvenioOut, status_code=201)
async def create_convenio(body: ConvenioCreate, db: AsyncSession = Depends(get_db)):
    # Cerrar convenio activo anterior de la misma OS
    stmt = select(Convenio).where(
        Convenio.obra_social_nro == body.obra_social_nro,
        Convenio.estado == "activo",
    )
    anterior = (await db.execute(stmt)).scalar_one_or_none()
    if anterior:
        anterior.estado = "cerrado"
        anterior.fecha_fin = body.fecha_inicio - datetime.timedelta(days=1)

    nuevo = Convenio(
        obra_social_nro=body.obra_social_nro,
        nombre=body.nombre,
        fecha_inicio=body.fecha_inicio,
        observacion=body.observacion,
        estado="activo",
    )
    db.add(nuevo)
    await db.commit()
    await db.refresh(nuevo)
    return nuevo


@router.get("/activo/{obra_social_nro}", response_model=ConvenioOut)
async def get_convenio_activo(obra_social_nro: int, db: AsyncSession = Depends(get_db)):
    stmt = select(Convenio).where(
        Convenio.obra_social_nro == obra_social_nro,
        Convenio.estado == "activo",
    )
    obj = (await db.execute(stmt)).scalar_one_or_none()
    if not obj:
        raise HTTPException(404, f"No hay convenio activo para OS {obra_social_nro}")
    return obj


@router.get("/{id}", response_model=ConvenioOut)
async def get_convenio(id: int, db: AsyncSession = Depends(get_db)):
    obj = await db.get(Convenio, id)
    if not obj:
        raise HTTPException(404, "Convenio no encontrado")
    return obj


@router.put("/{id}", response_model=ConvenioOut)
async def update_convenio(id: int, body: ConvenioUpdate, db: AsyncSession = Depends(get_db)):
    obj = await db.get(Convenio, id)
    if not obj:
        raise HTTPException(404, "Convenio no encontrado")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(obj, field, value)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.post("/{id}/cerrar", response_model=ConvenioOut)
async def cerrar_convenio(id: int, body: ConvenioCerrarIn, db: AsyncSession = Depends(get_db)):
    obj = await db.get(Convenio, id)
    if not obj:
        raise HTTPException(404, "Convenio no encontrado")
    if obj.estado == "cerrado":
        raise HTTPException(409, "El convenio ya está cerrado")
    obj.estado = "cerrado"
    obj.fecha_fin = body.fecha_fin or datetime.date.today()
    await db.commit()
    await db.refresh(obj)
    return obj
