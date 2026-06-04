import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models.nomenclador_cmc import Convenio, Galeno, ValorComponente
from app.modules.nomenclador import service
from app.modules.nomenclador.schemas import (
    GalenoActualizarPrecioIn,
    GalenoCreate,
    GalenoOut,
    GalenoUpdate,
)

router = APIRouter()


@router.get("/", response_model=List[GalenoOut])
async def list_galenos(
    obra_social_nro: Optional[int] = Query(None),
    convenio_id: Optional[int] = Query(None),
    tipo: Optional[str] = Query(None),
    codigo: Optional[str] = Query(None),
    vigente_a: Optional[datetime.date] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Galeno)
    if obra_social_nro:
        stmt = stmt.where(Galeno.obra_social_nro == obra_social_nro)
    if convenio_id:
        stmt = stmt.where(Galeno.convenio_id == convenio_id)
    if tipo:
        stmt = stmt.where(Galeno.tipo == tipo)
    if codigo:
        stmt = stmt.where(Galeno.codigo == codigo)
    if vigente_a:
        stmt = stmt.where(
            Galeno.vigencia_desde <= vigente_a,
            (Galeno.vigencia_hasta.is_(None)) | (Galeno.vigencia_hasta >= vigente_a),
        )
    stmt = stmt.order_by(Galeno.codigo, Galeno.vigencia_desde.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/", response_model=GalenoOut, status_code=201)
async def create_galeno(body: GalenoCreate, db: AsyncSession = Depends(get_db)):
    if not await db.get(Convenio, body.convenio_id):
        raise HTTPException(404, "Convenio no encontrado")
    obj = Galeno(**body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    # Generar la primera fila de historial para los valores que ya usen este galeno
    # (normalmente vacía en creación inicial; el historial se genera al crear el Valor)
    return obj


@router.get("/historial/{obra_social_nro}/{codigo}", response_model=List[GalenoOut])
async def historial_galeno(
    obra_social_nro: int, codigo: str, db: AsyncSession = Depends(get_db)
):
    stmt = select(Galeno).where(
        Galeno.obra_social_nro == obra_social_nro,
        Galeno.codigo == codigo,
    ).order_by(Galeno.vigencia_desde)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{id}", response_model=GalenoOut)
async def get_galeno(id: int, db: AsyncSession = Depends(get_db)):
    obj = await db.get(Galeno, id)
    if not obj:
        raise HTTPException(404, "Galeno no encontrado")
    return obj


@router.put("/{id}", response_model=GalenoOut)
async def update_galeno_metadata(id: int, body: GalenoUpdate, db: AsyncSession = Depends(get_db)):
    obj = await db.get(Galeno, id)
    if not obj:
        raise HTTPException(404, "Galeno no encontrado")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(obj, field, value)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.post("/{id}/actualizar_precio", response_model=GalenoOut)
async def actualizar_precio_galeno(
    id: int, body: GalenoActualizarPrecioIn, db: AsyncSession = Depends(get_db)
):
    """
    Cierra vigencia del galeno actual, crea uno nuevo y regenera historial_precio_codigo
    para todos los códigos que usaban este galeno. Todo en una sola transacción.
    """
    galeno_anterior = await db.get(Galeno, id)
    if not galeno_anterior:
        raise HTTPException(404, "Galeno no encontrado")
    if not galeno_anterior.activo:
        raise HTTPException(409, "El galeno no está activo")

    fecha_corte = body.vigencia_desde - datetime.timedelta(days=1)

    # 1. Cerrar vigencia anterior
    galeno_anterior.vigencia_hasta = fecha_corte
    galeno_anterior.activo = False
    await db.flush()

    # 2. Crear nuevo galeno con nuevo precio
    nuevo_galeno = Galeno(
        obra_social_nro=galeno_anterior.obra_social_nro,
        convenio_id=galeno_anterior.convenio_id,
        codigo=galeno_anterior.codigo,
        nombre=galeno_anterior.nombre,
        tipo=galeno_anterior.tipo,
        vigencia_desde=body.vigencia_desde,
        vigencia_hasta=None,
        valor_unitario=body.nuevo_valor_unitario,
        observacion=galeno_anterior.observacion,
    )
    db.add(nuevo_galeno)
    await db.flush()

    # 3. Regenerar historial (regla A)
    await service.regenerar_historial_por_galeno(
        galeno_id_anterior=id,
        nuevo_galeno_id=nuevo_galeno.id,
        vigencia_desde=body.vigencia_desde,
        db=db,
    )

    await db.commit()
    await db.refresh(nuevo_galeno)
    return nuevo_galeno


@router.delete("/{id}", status_code=204)
async def delete_galeno(id: int, db: AsyncSession = Depends(get_db)):
    obj = await db.get(Galeno, id)
    if not obj:
        raise HTTPException(404, "Galeno no encontrado")
    stmt = select(ValorComponente).where(
        ValorComponente.galeno_id == id,
        ValorComponente.activo == True,
    ).limit(1)
    if (await db.execute(stmt)).scalar_one_or_none():
        raise HTTPException(409, "El galeno tiene componentes activos apuntando a él")
    obj.activo = False
    await db.commit()
