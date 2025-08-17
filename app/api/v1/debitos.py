# from typing import List
# from fastapi import APIRouter, Depends, HTTPException, Query
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.db.crud import (
#     get_debitos,
#     get_debito,
#     create_debito,
#     update_debito,
#     delete_debito,
# )
# from app.schemas.main import DebitoCreate, DebitoUpdate, DebitoOut
# from app.api.deps import get_async_db

# router = APIRouter()

# @router.get("/", response_model=List[DebitoOut])
# async def list_debitos(
#     skip: int = Query(0, ge=0),
#     limit: int = Query(100, ge=1, le=500),
#     os_id: int | None = Query(None, description="Filtrar por obra social"),
#     periodo_id: int | None = Query(None, description="Filtrar por periodo"),
#     db: AsyncSession = Depends(get_async_db),
# ):
#     return await get_debitos(db, skip=skip, limit=limit, os_id=os_id, periodo_id=periodo_id)

# @router.post("/", response_model=DebitoOut, status_code=201)
# async def create_new_debito(
#     in_: DebitoCreate,
#     db: AsyncSession = Depends(get_async_db),
# ):
#     # Valida existencia previa si lo deseas...
#     return await create_debito(db, in_)

# @router.get("/{debito_id}", response_model=DebitoOut)
# async def read_debito(
#     debito_id: int,
#     db: AsyncSession = Depends(get_async_db),
# ):
#     db_obj = await get_debito(db, debito_id)
#     if not db_obj:
#         raise HTTPException(status_code=404, detail="Débito no encontrado")
#     return db_obj

# @router.patch("/{debito_id}", response_model=DebitoOut)
# async def edit_debito(
#     debito_id: int,
#     in_: DebitoUpdate,
#     db: AsyncSession = Depends(get_async_db),
# ):
#     db_obj = await get_debito(db, debito_id)
#     if not db_obj:
#         raise HTTPException(status_code=404, detail="Débito no encontrado")
#     return await update_debito(db, db_obj, in_)

# @router.delete("/{debito_id}", status_code=204)
# async def remove_debito(
#     debito_id: int,
#     db: AsyncSession = Depends(get_async_db),
# ):
#     await delete_debito(db, debito_id)
