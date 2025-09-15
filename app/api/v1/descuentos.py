# app/api/routers/descuentos.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from decimal import Decimal

from app.db.database import get_db
from app.db.models import Descuentos
from app.schemas.descuentos_especialidades_schemas import DescuentoIn, DescuentoInPatch, DescuentoOut, DescuentoUpdate

router = APIRouter()

@router.get("", response_model=List[DescuentoOut])
async def list_descuentos(db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(Descuentos))).scalars().all()

@router.get("/{desc_id}", response_model=DescuentoOut)
async def get_descuento(desc_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(Descuentos, desc_id)
    if not row:
        raise HTTPException(404, "Descuento no encontrado")
    return row

@router.get("/by_nro/{nro_colegio}", response_model=DescuentoOut)
async def get_descuento_by_nro(nro_colegio: int, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(
        select(Descuentos).where(Descuentos.nro_colegio == nro_colegio)
    )).scalars().first()
    if not row:
        raise HTTPException(404, "No existe descuento con ese nro de concepto")
    return row

@router.post("", response_model=DescuentoOut, status_code=201)
async def create_descuento(payload: DescuentoIn, db: AsyncSession = Depends(get_db)):
    row = Descuentos(
        nombre=payload.nombre,
        nro_colegio=payload.nro_colegio,
        precio=Decimal(str(payload.precio or 0)),
        porcentaje=Decimal(str(payload.porcentaje or 0)),
    )
    db.add(row)
    await db.flush()
    await db.commit()
    await db.refresh(row)
    return row

@router.patch("/{desc_id}", response_model=DescuentoOut)
async def patch_descuento(id: int, payload: DescuentoInPatch, db: AsyncSession = Depends(get_db)):
    row = await db.get(Descuentos, id)
    if not row:
        raise HTTPException(status_code=404, detail="No existe el descuento")
    if payload.precio is not None:
        row.precio = payload.precio
    if payload.porcentaje is not None:
        row.porcentaje = payload.porcentaje
    await db.commit()
    await db.refresh(row)
    return DescuentoOut(
        id=row.id,
        nro_colegio=row.nro_colegio,
        nombre=row.nombre,
        precio=row.precio,
        porcentaje=row.porcentaje,
    )

@router.delete("/{desc_id}", status_code=204)
async def delete_descuento(desc_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(Descuentos, desc_id)
    if not row:
        raise HTTPException(404, "Descuento no encontrado")
    await db.delete(row)
    await db.commit()
    return None
