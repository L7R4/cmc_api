# # from fastapi import APIRouter, Depends, Query
# # from sqlalchemy.ext.asyncio import AsyncSession
# # from app.db import crud
# # from app.schemas.main import MedicoOut
# # from app.api.deps import get_async_db

# # router = APIRouter(prefix="/medicos", tags=["medicos"])

# # @router.get("/", response_model=list[MedicoOut])
# # async def list_medicos(
# #     nombre: str | None = Query(None, description="Filtrar por nombre (contiene)"),
# #     nro_socio: int | None = Query(None, description="Filtrar por nro socio"),
# #     skip: int = Query(0, ge=0, description="Cuántos registros omitir (offset)"),
# #     limit: int = Query(10, ge=1, le=100, description="Cuántos registros devolver (página)"),
# #     db: AsyncSession = Depends(get_async_db),
# # ):
# #     """
# #     Lista médicos con filtros opcionales, ordenados A→Z por nombre,
# #     paginados con offset/limit.
# #     """
# #     return await crud.get_medicos(
# #         db,
# #         nombre=nombre,
# #         nro_socio=nro_socio,
# #         skip=skip,
# #         limit=limit,
# #     )


# from typing import List
# from fastapi import APIRouter, Depends, HTTPException, Query
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.db.crud import (
#     get_medicos,
#     get_medico,
#     create_medico,
#     update_medico,
#     delete_medico,
# )
# from app.schemas.main import MedicoCreate, MedicoUpdate, MedicoOut
# from app.api.deps import get_async_db

# router = APIRouter()

# @router.get("/", response_model=List[MedicoOut])
# async def list_medicos(
#     skip: int = Query(0, ge=0),
#     limit: int = Query(100, ge=1, le=100),
#     db: AsyncSession = Depends(get_async_db),
# ):
#     return await get_medicos(db, skip=skip, limit=limit)

# @router.post("/", response_model=MedicoOut, status_code=201)
# async def create_new_medico(
#     in_: MedicoCreate,
#     db: AsyncSession = Depends(get_async_db),
# ):
#     return await create_medico(db, in_)

# @router.get("/{medico_id}", response_model=MedicoOut)
# async def read_medico(
#     medico_id: int,
#     db: AsyncSession = Depends(get_async_db),
# ):
#     db_obj = await get_medico(db, medico_id)
#     if not db_obj:
#         raise HTTPException(status_code=404, detail="Médico no encontrado")
#     return db_obj

# @router.patch("/{medico_id}", response_model=MedicoOut)
# async def edit_medico(
#     medico_id: int,
#     in_: MedicoUpdate,
#     db: AsyncSession = Depends(get_async_db),
# ):
#     db_obj = await get_medico(db, medico_id)
#     if not db_obj:
#         raise HTTPException(status_code=404, detail="Médico no encontrado")
#     return await update_medico(db, db_obj, in_)

# @router.delete("/{medico_id}", status_code=204)
# async def remove_medico(
#     medico_id: int,
#     db: AsyncSession = Depends(get_async_db),
# ):
#     await delete_medico(db, medico_id)
