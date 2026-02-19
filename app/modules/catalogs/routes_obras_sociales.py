from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import ObrasSociales
from app.modules.catalogs.schemas import ObraSocialCreate, ObraSocialOut, ObraSocialUpdate

router = APIRouter()


@router.get("/", response_model=list[ObraSocialOut])
async def list_obras_sociales(
    db: AsyncSession = Depends(get_db),
    NRO_OBRASOCIAL: int | None = Query(None, description="Filtrar por N° obra social (exacto)"),
    OBRA_SOCIAL: str | None = Query(None, description="Filtrar por nombre (contiene)"),
):
    query = select(ObrasSociales).order_by(ObrasSociales.OBRA_SOCIAL.asc())
    if NRO_OBRASOCIAL is not None:
        query = query.where(ObrasSociales.NRO_OBRASOCIAL == NRO_OBRASOCIAL)
    if OBRA_SOCIAL is not None:
        query = query.where(ObrasSociales.OBRA_SOCIAL.ilike(f"%{OBRA_SOCIAL}%"))
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{id}", response_model=ObraSocialOut)
async def get_obra_social(
    id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> ObraSocialOut:
    res = await db.execute(select(ObrasSociales).where(ObrasSociales.ID == id))
    obj = res.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Obra social no encontrada")
    return obj


@router.post("/", response_model=ObraSocialOut, status_code=status.HTTP_201_CREATED)
async def create_obra_social(
    payload: ObraSocialCreate,
    db: AsyncSession = Depends(get_db),
) -> ObraSocialOut:
    data = payload.model_dump()
    nro = data.get("NRO_OBRASOCIAL")
    if nro is not None:
        res = await db.execute(select(ObrasSociales).where(ObrasSociales.NRO_OBRASOCIAL == nro))
        if res.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"Ya existe una obra social con NRO_OBRASOCIAL={nro}")
    obj = ObrasSociales(**data)
    db.add(obj)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Conflicto de integridad (índice único / constraint).") from e
    await db.refresh(obj)
    return obj


@router.patch("/{id}", response_model=ObraSocialOut)
async def update_obra_social(
    id: int = Path(..., ge=1),
    payload: ObraSocialUpdate = ...,
    db: AsyncSession = Depends(get_db),
) -> ObraSocialOut:
    res = await db.execute(select(ObrasSociales).where(ObrasSociales.ID == id))
    obj = res.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Obra social no encontrada")
    changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
    if "NRO_OBRASOCIAL" in changes and changes["NRO_OBRASOCIAL"] is not None:
        nuevo_nro = changes["NRO_OBRASOCIAL"]
        res_nro = await db.execute(
            select(ObrasSociales).where((ObrasSociales.NRO_OBRASOCIAL == nuevo_nro) & (ObrasSociales.ID != id))
        )
        if res_nro.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"Ya existe otra obra social con NRO_OBRASOCIAL={nuevo_nro}")
    for k, v in changes.items():
        setattr(obj, k, v)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Conflicto de integridad al actualizar.") from e
    await db.refresh(obj)
    return obj


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_obra_social(
    id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> Response:
    res = await db.execute(select(ObrasSociales).where(ObrasSociales.ID == id))
    obj = res.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Obra social no encontrada")
    await db.delete(obj)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
