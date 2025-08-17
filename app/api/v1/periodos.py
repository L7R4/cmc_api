# from fastapi import APIRouter, Depends, HTTPException, Query
# from sqlalchemy.ext.asyncio import AsyncSession
# from typing import List

# from app.db.crud import (
#     get_periodos, get_periodo,
#     create_periodo, update_periodo, delete_periodo
# )
# from app.api.deps import get_async_db
# from app.schemas.main import PeriodoCreate, PeriodoUpdate, PeriodoOut

# router = APIRouter()

# @router.get("/", response_model=List[PeriodoOut])
# async def read_periodos(
#     skip: int = Query(0, ge=0),
#     limit: int = Query(10, ge=1, le=100),
#     db: AsyncSession = Depends(get_async_db),
# ):
#     return await get_periodos(db, skip, limit)

# @router.post("/", response_model=PeriodoOut)
# async def create_new_periodo(
#     in_: PeriodoCreate,
#     db: AsyncSession = Depends(get_async_db),
# ):
#     return await create_periodo(db, in_)

# @router.get("/{periodo_id}", response_model=PeriodoOut)
# async def read_periodo(
#     periodo_id: int,
#     db: AsyncSession = Depends(get_async_db),
# ):
#     db_obj = await get_periodo(db, periodo_id)
#     if not db_obj:
#         raise HTTPException(404, "Periodo no encontrado")
#     return db_obj

# @router.patch("/{periodo_id}", response_model=PeriodoOut)
# async def edit_periodo(
#     periodo_id: int,
#     in_: PeriodoUpdate,
#     db: AsyncSession = Depends(get_async_db),
# ):
#     db_obj = await get_periodo(db, periodo_id)
#     if not db_obj:
#         raise HTTPException(404, "Periodo no encontrado")
#     return await update_periodo(db, db_obj, in_)

# @router.delete("/{periodo_id}", status_code=204)
# async def remove_periodo(
#     periodo_id: int,
#     db: AsyncSession = Depends(get_async_db),
# ):
#     await delete_periodo(db, periodo_id)
