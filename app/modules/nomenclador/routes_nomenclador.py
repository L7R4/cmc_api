from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.database import get_db
from app.db.models.nomenclador_cmc import (
    MedicoCodigoHabilitado,
    NomencladorCMC,
    NomencladorEspecialidad,
    Valor,
)
from app.modules.nomenclador.schemas import (
    MedicoHabilitacionCreate,
    MedicoHabilitacionOut,
    MedicoHabilitacionUpdate,
    NomencladorConVariantesOut,
    NomencladorCreate,
    NomencladorEspecialidadCreate,
    NomencladorEspecialidadOut,
    NomencladorOut,
    NomencladorUpdate,
)

router = APIRouter()


# ── Nomenclador CRUD ──────────────────────────────────────────────────────────

@router.get("/", response_model=List[NomencladorOut])
async def list_nomenclador(
    q: Optional[str] = Query(None),
    categoria: Optional[str] = Query(None),
    complejidad: Optional[str] = Query(None),
    activo: Optional[bool] = Query(True),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(NomencladorCMC)
    if q:
        stmt = stmt.where(
            NomencladorCMC.codigo.contains(q) | NomencladorCMC.descripcion.contains(q)
        )
    if categoria:
        stmt = stmt.where(NomencladorCMC.categoria == categoria)
    if complejidad:
        stmt = stmt.where(NomencladorCMC.complejidad == complejidad)
    if activo is not None:
        stmt = stmt.where(NomencladorCMC.activo == activo)
    stmt = stmt.offset((page - 1) * size).limit(size)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/", response_model=NomencladorOut, status_code=201)
async def create_nomenclador(body: NomencladorCreate, db: AsyncSession = Depends(get_db)):
    if body.proviene_de_id is not None:
        raiz = await db.get(NomencladorCMC, body.proviene_de_id)
        if not raiz:
            raise HTTPException(404, "Código raíz no encontrado")
    obj = NomencladorCMC(**body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.get("/{id}", response_model=NomencladorConVariantesOut)
async def get_nomenclador(id: int, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(NomencladorCMC)
        .where(NomencladorCMC.id == id)
        .options(selectinload(NomencladorCMC.variantes))
    )
    obj = (await db.execute(stmt)).scalar_one_or_none()
    if not obj:
        raise HTTPException(404, "Código no encontrado")
    return obj


@router.put("/{id}", response_model=NomencladorOut)
async def update_nomenclador(id: int, body: NomencladorUpdate, db: AsyncSession = Depends(get_db)):
    obj = await db.get(NomencladorCMC, id)
    if not obj:
        raise HTTPException(404, "Código no encontrado")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(obj, field, value)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.patch("/{id}/activar", response_model=NomencladorOut)
async def toggle_activo_nomenclador(
    id: int, activo: bool, db: AsyncSession = Depends(get_db)
):
    obj = await db.get(NomencladorCMC, id)
    if not obj:
        raise HTTPException(404, "Código no encontrado")
    obj.activo = activo
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/{id}", status_code=204)
async def delete_nomenclador(id: int, db: AsyncSession = Depends(get_db)):
    obj = await db.get(NomencladorCMC, id)
    if not obj:
        raise HTTPException(404, "Código no encontrado")
    stmt = select(Valor).where(Valor.nomenclador_id == id, Valor.estado == "activo").limit(1)
    if (await db.execute(stmt)).scalar_one_or_none():
        raise HTTPException(409, "El código tiene valores activos; ciérrelos antes de eliminarlo")
    stmt_variantes = select(NomencladorCMC).where(NomencladorCMC.proviene_de_id == id).limit(1)
    if (await db.execute(stmt_variantes)).scalar_one_or_none():
        raise HTTPException(409, "El código tiene variantes; elimínelas primero")
    await db.delete(obj)
    await db.commit()


# ── Especialidades habilitadas ────────────────────────────────────────────────

@router.get("/{id}/especialidades", response_model=List[NomencladorEspecialidadOut])
async def list_especialidades(id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(NomencladorEspecialidad).where(
        NomencladorEspecialidad.nomenclador_id == id,
        NomencladorEspecialidad.activo == True,
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/{id}/especialidades", response_model=NomencladorEspecialidadOut, status_code=201)
async def add_especialidad(
    id: int, body: NomencladorEspecialidadCreate, db: AsyncSession = Depends(get_db)
):
    obj = await db.get(NomencladorCMC, id)
    if not obj:
        raise HTTPException(404, "Código no encontrado")
    # Reactivar si ya existía soft-deleted
    stmt = select(NomencladorEspecialidad).where(
        NomencladorEspecialidad.nomenclador_id == id,
        NomencladorEspecialidad.especialidad_id_colegio == body.especialidad_id_colegio,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        existing.activo = True
        existing.observacion = body.observacion
        await db.commit()
        await db.refresh(existing)
        return existing
    ne = NomencladorEspecialidad(
        nomenclador_id=id,
        especialidad_id_colegio=body.especialidad_id_colegio,
        observacion=body.observacion,
    )
    db.add(ne)
    await db.commit()
    await db.refresh(ne)
    return ne


@router.patch("/{id}/especialidades/{esp_id}/activar", response_model=NomencladorEspecialidadOut)
async def toggle_especialidad(
    id: int, esp_id: int, activo: bool, db: AsyncSession = Depends(get_db)
):
    stmt = select(NomencladorEspecialidad).where(
        NomencladorEspecialidad.nomenclador_id == id,
        NomencladorEspecialidad.especialidad_id_colegio == esp_id,
    )
    obj = (await db.execute(stmt)).scalar_one_or_none()
    if not obj:
        raise HTTPException(404, "Habilitación no encontrada")
    obj.activo = activo
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/{id}/especialidades/{esp_id}", status_code=204)
async def delete_especialidad(id: int, esp_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(NomencladorEspecialidad).where(
        NomencladorEspecialidad.nomenclador_id == id,
        NomencladorEspecialidad.especialidad_id_colegio == esp_id,
    )
    obj = (await db.execute(stmt)).scalar_one_or_none()
    if not obj:
        raise HTTPException(404, "Habilitación no encontrada")
    await db.delete(obj)
    await db.commit()


# ── Habilitaciones por médico ─────────────────────────────────────────────────

@router.get("/{id}/habilitaciones_medico", response_model=List[MedicoHabilitacionOut])
async def list_habilitaciones_medico(
    id: int,
    activo: Optional[bool] = Query(True),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(MedicoCodigoHabilitado).where(
        MedicoCodigoHabilitado.nomenclador_id == id,
    )
    if activo is not None:
        stmt = stmt.where(MedicoCodigoHabilitado.activo == activo)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/{id}/habilitaciones_medico", response_model=MedicoHabilitacionOut, status_code=201)
async def create_habilitacion_medico(
    id: int, body: MedicoHabilitacionCreate, db: AsyncSession = Depends(get_db)
):
    if not await db.get(NomencladorCMC, id):
        raise HTTPException(404, "Código no encontrado")
    obj = MedicoCodigoHabilitado(nomenclador_id=id, **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.put("/{id}/habilitaciones_medico/{hab_id}", response_model=MedicoHabilitacionOut)
async def update_habilitacion_medico(
    id: int, hab_id: int, body: MedicoHabilitacionUpdate, db: AsyncSession = Depends(get_db)
):
    obj = await db.get(MedicoCodigoHabilitado, hab_id)
    if not obj or obj.nomenclador_id != id:
        raise HTTPException(404, "Habilitación no encontrada")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(obj, field, value)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.patch("/{id}/habilitaciones_medico/{hab_id}/activar", response_model=MedicoHabilitacionOut)
async def toggle_habilitacion_medico(
    id: int, hab_id: int, activo: bool, db: AsyncSession = Depends(get_db)
):
    obj = await db.get(MedicoCodigoHabilitado, hab_id)
    if not obj or obj.nomenclador_id != id:
        raise HTTPException(404, "Habilitación no encontrada")
    obj.activo = activo
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/{id}/habilitaciones_medico/{hab_id}", status_code=204)
async def delete_habilitacion_medico(id: int, hab_id: int, db: AsyncSession = Depends(get_db)):
    obj = await db.get(MedicoCodigoHabilitado, hab_id)
    if not obj or obj.nomenclador_id != id:
        raise HTTPException(404, "Habilitación no encontrada")
    await db.delete(obj)
    await db.commit()
