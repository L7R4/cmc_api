# routers/debitos_creditos.py
from fastapi import APIRouter, Depends, HTTPException, Query, Path
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from typing import List, Optional

from app.db.database import get_db
from app.db.models import Debito_Credito, DetalleLiquidacion, Liquidacion, GuardarAtencion
from app.schemas.debitos_creditos_schema import (
    DebitoCreditoOut, DebitoCreditoCreateByDetalle, DebitoCreditoUpdate
)
from app.services.liquidaciones import (
    recomputar_pagado_detalle, recomputar_totales_de_liquidacion, recomputar_todo_de_liquidacion
)

router = APIRouter(prefix="/api/debitos_creditos", tags=["Débitos/Créditos"])

# ---------------------------
# 1) Listado con filtros
# ---------------------------
@router.get("", response_model=List[DebitoCreditoOut])
async def listar_debitos_creditos(
    db: AsyncSession = Depends(get_db),
    obra_social_id: Optional[int] = None,
    periodo: Optional[str] = Query(None, description="YYYY-MM"),
    liquidacion_id: Optional[int] = None,
    detalle_id: Optional[int] = None,
    medico_id: Optional[int] = None,
    skip: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=5000),
):
    stmt = (
        select(
            Debito_Credito,
            DetalleLiquidacion.id.label("detalle_id"),
            DetalleLiquidacion.liquidacion_id.label("liquidacion_id"),
            DetalleLiquidacion.medico_id.label("medico_id"),
        )
        .join(DetalleLiquidacion, DetalleLiquidacion.debito_credito_id == Debito_Credito.id, isouter=True)
        .offset(skip).limit(limit)
    )
    if obra_social_id is not None:
        stmt = stmt.where(Debito_Credito.obra_social_id == obra_social_id)
    if periodo:
        stmt = stmt.where(Debito_Credito.periodo == periodo)
    if liquidacion_id is not None:
        stmt = stmt.where(DetalleLiquidacion.liquidacion_id == liquidacion_id)
    if detalle_id is not None:
        stmt = stmt.where(DetalleLiquidacion.id == detalle_id)
    if medico_id is not None:
        stmt = stmt.where(DetalleLiquidacion.medico_id == medico_id)

    res = await db.execute(stmt)
    out: List[DebitoCreditoOut] = []
    for dc, det_id, liq_id, med_id in res.all():
        item = DebitoCreditoOut.from_orm(dc)
        item.detalle_id = det_id
        item.liquidacion_id = liq_id
        item.medico_id = med_id
        out.append(item)
    return out

# ---------------------------
# 2) Obtener por id
# ---------------------------
@router.get("/{debcre_id}", response_model=DebitoCreditoOut)
async def obtener_debito_credito(
    debcre_id: int,
    db: AsyncSession = Depends(get_db),
):
    dc = await db.get(Debito_Credito, debcre_id)
    if not dc:
        raise HTTPException(404, "No encontrado")

    # buscar join con detalle (si existe)
    det = await db.execute(
        select(DetalleLiquidacion.id, DetalleLiquidacion.liquidacion_id, DetalleLiquidacion.medico_id)
        .where(DetalleLiquidacion.debito_credito_id == dc.id)
        .limit(1)
    )
    join_row = det.first()
    out = DebitoCreditoOut.from_orm(dc)
    if join_row:
        out.detalle_id = join_row.id
        out.liquidacion_id = join_row.liquidacion_id
        out.medico_id = join_row.medico_id
    return out

# ---------------------------
# 3) Crear POR DETALLE (recomendado)
# ---------------------------
@router.post("/by_detalle", response_model=DebitoCreditoOut, status_code=201)
async def crear_debito_credito_por_detalle(
    payload: DebitoCreditoCreateByDetalle,
    db: AsyncSession = Depends(get_db),
):
    det = await db.get(DetalleLiquidacion, payload.detalle_id)
    if not det:
        raise HTTPException(404, "Detalle no encontrado")

    if det.debito_credito_id is not None:
        raise HTTPException(409, "El detalle ya tiene un débito/crédito asignado")

    liq = await db.get(Liquidacion, det.liquidacion_id)
    if not liq:
        raise HTTPException(404, "Liquidación no encontrada")

    # Derivar id_atencion, OS y periodo desde el detalle/liquidación
    try:
        id_atencion = int(det.prestacion_id)
    except Exception:
        raise HTTPException(400, "prestacion_id no es un entero válido")

    periodo_norm = f"{liq.anio_periodo:04d}-{liq.mes_periodo:02d}"

    # Validación cruzada (opcional): la atención debe existir y coincidir OS/periodo
    ga = await db.execute(
        select(
            GuardarAtencion.ID,
            GuardarAtencion.NRO_OBRA_SOCIAL,
            GuardarAtencion.ANIO_PERIODO,
            GuardarAtencion.MES_PERIODO,
        ).where(GuardarAtencion.ID == id_atencion)
    )
    ga_row = ga.first()
    if not ga_row:
        raise HTTPException(404, "id_atencion inexistente")
    periodo_ga = f"{ga_row.ANIO_PERIODO:04d}-{ga_row.MES_PERIODO:02d}"
    if ga_row.NRO_OBRA_SOCIAL != liq.obra_social_id or periodo_ga != periodo_norm:
        raise HTTPException(400, "La atención no coincide con OS/período de la liquidación")

    # Crear DC
    dc = Debito_Credito(
        tipo=payload.tipo,
        id_atencion=id_atencion,
        obra_social_id=liq.obra_social_id,
        periodo=periodo_norm,
        monto=payload.monto,
        observacion=payload.observacion,
        creado_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        created_by_user=payload.created_by_user,
    )
    db.add(dc)
    await db.flush()  # obtener dc.id

    # Mapear al detalle (1:1)
    det.debito_credito_id = dc.id
    await recomputar_todo_de_liquidacion(db, det.liquidacion_id)

    await db.commit()
    await db.refresh(dc)

    out = DebitoCreditoOut.from_orm(dc)
    out.detalle_id = det.id
    out.liquidacion_id = det.liquidacion_id
    out.medico_id = det.medico_id
    return out

# ---------------------------
# 4) Editar (y/o Remapear)
# ---------------------------
@router.put("/{debcre_id}", response_model=DebitoCreditoOut)
async def editar_debito_credito(
    debcre_id: int,
    payload: DebitoCreditoUpdate,
    db: AsyncSession = Depends(get_db),
):
    dc = await db.get(Debito_Credito, debcre_id)
    if not dc:
        raise HTTPException(404, "No encontrado")

    # localizar detalle actual (si lo hubiera)
    q_det = await db.execute(
        select(DetalleLiquidacion).where(DetalleLiquidacion.debito_credito_id == dc.id).limit(1)
    )
    det_actual: Optional[DetalleLiquidacion] = q_det.scalars().first()
    liq_id_a_recalcular: set[int] = set()

    # ¿Remapeo?
    if payload.detalle_id is not None and (not det_actual or det_actual.id != payload.detalle_id):
        det_nuevo = await db.get(DetalleLiquidacion, payload.detalle_id)
        if not det_nuevo:
            raise HTTPException(404, "Detalle destino no encontrado")
        if det_nuevo.debito_credito_id is not None:
            raise HTTPException(409, "El detalle destino ya tiene un DC asignado")

        # liberar del detalle anterior
        if det_actual:
            det_actual.debito_credito_id = None
            await recomputar_pagado_detalle(db, det_actual.id)
            liq_id_a_recalcular.add(det_actual.liquidacion_id)

        # ajustar DC a la nueva atención/OS/periodo
        liq_nueva = await db.get(Liquidacion, det_nuevo.liquidacion_id)
        try:
            id_atencion = int(det_nuevo.prestacion_id)
        except Exception:
            raise HTTPException(400, "prestacion_id del detalle destino no es entero válido")

        dc.id_atencion = id_atencion
        dc.obra_social_id = liq_nueva.obra_social_id
        dc.periodo = f"{liq_nueva.anio_periodo:04d}-{liq_nueva.mes_periodo:02d}"

        # mapear
        det_nuevo.debito_credito_id = dc.id
        await recomputar_pagado_detalle(db, det_nuevo.id)
        liq_id_a_recalcular.add(det_nuevo.liquidacion_id)

        det_actual = det_nuevo  # ahora el “actual” es el nuevo

    # Actualizar campos simples
    data = payload.dict(exclude_unset=True, exclude={"detalle_id"})
    for k, v in data.items():
        setattr(dc, k, v)

    # Recompute pagado/totales
    if det_actual:
        await recomputar_pagado_detalle(db, det_actual.id)
        liq_id_a_recalcular.add(det_actual.liquidacion_id)

    for _liq_id in liq_id_a_recalcular:
        await recomputar_totales_de_liquidacion(db, _liq_id)

    await db.commit()
    await db.refresh(dc)

    out = DebitoCreditoOut.from_orm(dc)
    if det_actual:
        out.detalle_id = det_actual.id
        out.liquidacion_id = det_actual.liquidacion_id
        out.medico_id = det_actual.medico_id
    return out

# ---------------------------
# 5) Eliminar
# ---------------------------
@router.delete("/{debcre_id}", status_code=204)
async def borrar_debito_credito(
    debcre_id: int,
    db: AsyncSession = Depends(get_db),
):
    dc = await db.get(Debito_Credito, debcre_id)
    if not dc:
        raise HTTPException(404, "No encontrado")

    # desmapear del detalle si existiera
    q_det = await db.execute(
        select(DetalleLiquidacion).where(DetalleLiquidacion.debito_credito_id == dc.id).limit(1)
    )
    det: Optional[DetalleLiquidacion] = q_det.scalars().first()
    liq_id: Optional[int] = None
    if det:
        liq_id = det.liquidacion_id
        det.debito_credito_id = None
        await recomputar_pagado_detalle(db, det.id)

    await db.delete(dc)

    if liq_id is not None:
        await recomputar_totales_de_liquidacion(db, liq_id)

    await db.commit()
    return None
