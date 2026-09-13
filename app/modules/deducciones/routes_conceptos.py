from decimal import Decimal
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import Conceptos
from app.modules.deducciones.schemas import (
    ConceptoIn,
    ConceptoInPatch,
    ConceptoOut,
    ConceptoUpdate,
)

router = APIRouter()


@router.get("", response_model=List[ConceptoOut])
async def list_conceptos(db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(Conceptos))).scalars().all()


@router.get("/{concepto_id}", response_model=ConceptoOut)
async def get_concepto(concepto_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(Conceptos, concepto_id)
    if not row:
        raise HTTPException(404, "Concepto no encontrado")
    return row


@router.get("/by_nro/{nro_colegio}", response_model=ConceptoOut)
async def get_concepto_by_nro(nro_colegio: int, db: AsyncSession = Depends(get_db)):
    row = (
        await db.execute(select(Conceptos).where(Conceptos.nro_colegio == nro_colegio))
    ).scalars().first()
    if not row:
        raise HTTPException(404, "No existe concepto con ese nro de concepto")
    return row


@router.post("", response_model=ConceptoOut, status_code=201)
async def create_concepto(payload: ConceptoIn, db: AsyncSession = Depends(get_db)):
    row = Conceptos(
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


@router.patch("/{concepto_id}", response_model=ConceptoOut)
async def patch_concepto(concepto_id: int, payload: ConceptoInPatch, db: AsyncSession = Depends(get_db)):
    row = await db.get(Conceptos, concepto_id)
    if not row:
        raise HTTPException(status_code=404, detail="No existe el concepto")
    if payload.precio is not None:
        row.precio = payload.precio
    if payload.porcentaje is not None:
        row.porcentaje = payload.porcentaje
    await db.commit()
    await db.refresh(row)
    return ConceptoOut(
        id=row.id,
        nro_colegio=row.nro_colegio,
        nombre=row.nombre,
        precio=row.precio,
        porcentaje=row.porcentaje,
    )


@router.delete("/{concepto_id}", status_code=204)
async def delete_concepto(concepto_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(Conceptos, concepto_id)
    if not row:
        raise HTTPException(404, "Concepto no encontrado")
    await db.delete(row)
    await db.commit()
    return None
