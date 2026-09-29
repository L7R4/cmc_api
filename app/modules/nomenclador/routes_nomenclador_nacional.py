from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models.nomenclador_cmc import NomencladorCMC, NomencladorNacional
from app.modules.nomenclador.schemas import (
    NomencladorNacionalCreate,
    NomencladorNacionalOut,
    NomencladorNacionalUpdate,
)

router = APIRouter()


@router.get("/", response_model=List[NomencladorNacionalOut])
async def list_nomenclador_nacional(
    q: Optional[str] = Query(None, description="Busca en código o descripción"),
    activo: Optional[bool] = Query(
        None,
        description="Omitido = catálogo completo (activos e inactivos).",
    ),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Catálogo del Nomenclador Nacional — independiente del catálogo del Colegio
    (`GET /nomenclador/`). Uno o varios códigos del Colegio pueden vincularse a la
    misma fila de acá (`NomencladorCMC.nomenclador_nacional_id`)."""
    stmt = select(NomencladorNacional)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            NomencladorNacional.codigo.contains(q) | NomencladorNacional.descripcion.ilike(like)
        )
    if activo is not None:
        stmt = stmt.where(NomencladorNacional.activo == activo)
    stmt = stmt.order_by(NomencladorNacional.codigo).offset((page - 1) * size).limit(size)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/codigos", response_model=List[str])
async def list_codigos_nacionales(
    activo: Optional[bool] = Query(True),
    db: AsyncSession = Depends(get_db),
):
    """Solo los códigos NN (para autocompletar el vínculo desde el alta de un código
    del Colegio)."""
    stmt = select(NomencladorNacional.codigo)
    if activo is not None:
        stmt = stmt.where(NomencladorNacional.activo == activo)
    result = await db.execute(stmt)
    return [row[0] for row in result.all()]


@router.post("/", response_model=NomencladorNacionalOut, status_code=201)
async def create_nomenclador_nacional(
    body: NomencladorNacionalCreate, db: AsyncSession = Depends(get_db)
):
    ya_existe = (
        await db.execute(select(NomencladorNacional).where(NomencladorNacional.codigo == body.codigo))
    ).scalar_one_or_none()
    if ya_existe:
        raise HTTPException(
            409,
            f"El código NN {body.codigo} ya existe (id {ya_existe.id}). "
            "Usá otro número o editá el existente.",
        )
    obj = NomencladorNacional(**body.model_dump())
    db.add(obj)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            409, f"El código NN {body.codigo} ya existe. Actualizá la lista y reintentá."
        )
    await db.refresh(obj)
    return obj


@router.get("/{id}", response_model=NomencladorNacionalOut)
async def get_nomenclador_nacional(id: int, db: AsyncSession = Depends(get_db)):
    obj = await db.get(NomencladorNacional, id)
    if not obj:
        raise HTTPException(404, "Código NN no encontrado")
    return obj


@router.put("/{id}", response_model=NomencladorNacionalOut)
async def update_nomenclador_nacional(
    id: int, body: NomencladorNacionalUpdate, db: AsyncSession = Depends(get_db)
):
    obj = await db.get(NomencladorNacional, id)
    if not obj:
        raise HTTPException(404, "Código NN no encontrado")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(obj, field, value)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.patch("/{id}/activar", response_model=NomencladorNacionalOut)
async def toggle_activo_nomenclador_nacional(
    id: int, activo: bool, db: AsyncSession = Depends(get_db)
):
    obj = await db.get(NomencladorNacional, id)
    if not obj:
        raise HTTPException(404, "Código NN no encontrado")
    obj.activo = activo
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/{id}", status_code=204)
async def delete_nomenclador_nacional(id: int, db: AsyncSession = Depends(get_db)):
    obj = await db.get(NomencladorNacional, id)
    if not obj:
        raise HTTPException(404, "Código NN no encontrado")
    stmt = (
        select(NomencladorCMC.id)
        .where(NomencladorCMC.nomenclador_nacional_id == id)
        .limit(1)
    )
    if (await db.execute(stmt)).scalar_one_or_none():
        raise HTTPException(
            409,
            "Hay códigos del Colegio vinculados a este NN; desvinculalos antes de eliminarlo",
        )
    await db.delete(obj)
    await db.commit()
