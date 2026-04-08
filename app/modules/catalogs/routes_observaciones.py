from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models.catalogs import BoletinObservacion, BoletinObservacionPlantilla
from app.modules.catalogs.schemas import ObservacionIn, ObservacionOut, PlantillaIn, PlantillaOut

router = APIRouter()


# ── Observaciones ────────────────────────────────────────────────

@router.get(
    "/observaciones",
    response_model=dict,
    summary="Listar observaciones por obra social",
)
async def listar_observaciones(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(BoletinObservacion))).scalars().all()
    return {"items": [ObservacionOut.model_validate(r) for r in rows]}


@router.put(
    "/observaciones/{nro_obrasocial}",
    response_model=ObservacionOut,
    summary="Crear o actualizar observación de una obra social",
)
async def upsert_observacion(
    nro_obrasocial: int,
    body: ObservacionIn,
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(BoletinObservacion, nro_obrasocial)
    if row is None:
        row = BoletinObservacion(nro_obrasocial=nro_obrasocial, texto=body.texto)
        db.add(row)
    else:
        row.texto = body.texto
    await db.commit()
    await db.refresh(row)
    return ObservacionOut.model_validate(row)


@router.delete(
    "/observaciones/{nro_obrasocial}",
    status_code=204,
    summary="Eliminar observación de una obra social",
)
async def eliminar_observacion(
    nro_obrasocial: int,
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(BoletinObservacion, nro_obrasocial)
    if row is not None:
        await db.delete(row)
        await db.commit()
    return Response(status_code=204)


# ── Plantillas ───────────────────────────────────────────────────

@router.get(
    "/observaciones/plantillas",
    response_model=dict,
    summary="Listar plantillas de observación",
)
async def listar_plantillas(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(BoletinObservacionPlantilla))).scalars().all()
    return {"items": [PlantillaOut.model_validate(r) for r in rows]}


@router.post(
    "/observaciones/plantillas",
    response_model=PlantillaOut,
    status_code=201,
    summary="Crear plantilla de observación",
)
async def crear_plantilla(
    body: PlantillaIn,
    db: AsyncSession = Depends(get_db),
):
    plantilla = BoletinObservacionPlantilla(texto=body.texto)
    db.add(plantilla)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Ya existe una plantilla con ese texto")
    await db.refresh(plantilla)
    return PlantillaOut.model_validate(plantilla)


@router.delete(
    "/observaciones/plantillas/{id}",
    status_code=204,
    summary="Eliminar plantilla de observación",
)
async def eliminar_plantilla(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(BoletinObservacionPlantilla, id)
    if row is not None:
        await db.delete(row)
        await db.commit()
    return Response(status_code=204)
