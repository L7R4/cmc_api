from typing import List

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import Especialidad
from app.modules.catalogs.schemas import (
    EspecialidadCreate,
    EspecialidadOut,
    EspecialidadUpdate,
)

router = APIRouter()


# ── Helpers ────────────────────────────────────────────────────────────────

def _out(obj: Especialidad) -> dict:
    return {
        "id": int(obj.ID),
        "id_colegio_espe": int(obj.ID_COLEGIO_ESPE),
        "nombre": str(obj.ESPECIALIDAD),
    }


async def _get_or_404(id: int, db: AsyncSession) -> Especialidad:
    obj = (
        await db.execute(select(Especialidad).where(Especialidad.ID == id))
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Especialidad no encontrada")
    return obj


async def _chequear_id_colegio_libre(
    db: AsyncSession, id_colegio_espe: int, excluir_id: int | None = None
) -> None:
    """409 si otra especialidad ya usa ese `ID_COLEGIO_ESPE`.

    `ID_COLEGIO_ESPE` es la clave con la que el resto del sistema referencia a
    la especialidad (`listado_medico.NRO_ESPECIALIDAD1..6` y el JSON
    `conceps_espec`), pero en la tabla es un `KEY`, no un `UNIQUE`: la unicidad
    depende de este SELECT. Duplicarlo haría ambigua esa referencia.
    """
    stmt = select(Especialidad).where(Especialidad.ID_COLEGIO_ESPE == id_colegio_espe)
    if excluir_id is not None:
        stmt = stmt.where(Especialidad.ID != excluir_id)
    if (await db.execute(stmt)).scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Ya existe una especialidad con id_colegio_espe={id_colegio_espe}",
        )


# ── Rutas ──────────────────────────────────────────────────────────────────

@router.get("/", response_model=List[EspecialidadOut])
async def list_especialidades(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Especialidad))).scalars().all()
    return [_out(r) for r in rows]


@router.post("/", response_model=EspecialidadOut, status_code=status.HTTP_201_CREATED)
async def create_especialidad(
    payload: EspecialidadCreate,
    db: AsyncSession = Depends(get_db),
):
    await _chequear_id_colegio_libre(db, payload.id_colegio_espe)

    obj = Especialidad(
        ID_COLEGIO_ESPE=payload.id_colegio_espe,
        ESPECIALIDAD=payload.nombre,
    )
    db.add(obj)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Conflicto de integridad.") from e

    await db.refresh(obj)
    return _out(obj)


@router.patch("/{id}", response_model=EspecialidadOut)
async def update_especialidad(
    id: int = Path(..., ge=1),
    payload: EspecialidadUpdate = ...,
    db: AsyncSession = Depends(get_db),
):
    obj = await _get_or_404(id, db)

    if payload.id_colegio_espe is not None:
        await _chequear_id_colegio_libre(db, payload.id_colegio_espe, excluir_id=id)
        obj.ID_COLEGIO_ESPE = payload.id_colegio_espe

    if payload.nombre is not None:
        obj.ESPECIALIDAD = payload.nombre

    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Conflicto de integridad al actualizar."
        ) from e

    await db.refresh(obj)
    return _out(obj)
