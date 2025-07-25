from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.crud import (
    get_descuentos,
    get_descuento,
    create_descuento,
    update_descuento,
    delete_descuento,
    bulk_create_descuentos,
    get_medicos,
)
from app.schemas.main import DescuentoCreate, DescuentoUpdate, DescuentoOut
from app.api.deps import get_async_db

router = APIRouter()

@router.get("/", response_model=List[DescuentoOut])
async def list_descuentos(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    concepto_id: int | None = Query(None, description="Filtrar por concepto"),
    periodo_id: int | None = Query(None, description="Filtrar por periodo"),
    db: AsyncSession = Depends(get_async_db),
):
    return await get_descuentos(db, skip=skip, limit=limit, concepto_id=concepto_id, periodo_id=periodo_id)

@router.post("/", response_model=DescuentoOut, status_code=201)
async def create_new_descuento(
    in_: DescuentoCreate,
    db: AsyncSession = Depends(get_async_db),
):
    return await create_descuento(db, in_)

@router.get("/{descuento_id}", response_model=DescuentoOut)
async def read_descuento(
    descuento_id: int,
    db: AsyncSession = Depends(get_async_db),
):
    db_obj = await get_descuento(db, descuento_id)
    if not db_obj:
        raise HTTPException(status_code=404, detail="Descuento no encontrado")
    return db_obj

@router.patch("/{descuento_id}", response_model=DescuentoOut)
async def edit_descuento(
    descuento_id: int,
    in_: DescuentoUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    db_obj = await get_descuento(db, descuento_id)
    if not db_obj:
        raise HTTPException(status_code=404, detail="Descuento no encontrado")
    return await update_descuento(db, db_obj, in_)

@router.delete("/{descuento_id}", status_code=204)
async def remove_descuento(
    descuento_id: int,
    db: AsyncSession = Depends(get_async_db),
):
    await delete_descuento(db, descuento_id)

@router.post("/generar/{concepto_id}", response_model=List[DescuentoOut])
async def generar_descuentos(
    concepto_id: int,
    periodo_id: int = Query(..., description="ID del periodo"),
    db: AsyncSession = Depends(get_async_db),
):
    """
    —— WEPSSS —— Genera un descuento de ese concepto para TODOS los médicos en el periodo.
    """
    medicos = await get_medicos(db, skip=0, limit=1000)
    return await bulk_create_descuentos(db, concepto_id, periodo_id, medicos)
