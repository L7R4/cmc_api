import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import (
    DetalleLiquidacion,
    Liquidacion,
    LoteAjuste,
    Pago,
    Periodos,
    Recibo,
)
from app.modules.liquidacion.schemas import (
    DetalleLiquidacionRead,
    DetalleVistaRow,
    LiquidacionCreate,
    LiquidacionRead,
    ReciboAnularPayload,
    ReciboRead,
)
from app.modules.liquidacion.service import (
    build_detalles_liquidacion,
    detalle_recibo_medico,
    recalcular_totales_de_liquidacion,
    vista_detalles_liquidacion,
)

router = APIRouter()


# ================================================
# POST: Crear una liquidación por obra social
# ================================================
@router.post("/liquidaciones_por_os/crear", response_model=LiquidacionRead, status_code=201)
async def crear_liquidacion(payload: LiquidacionCreate, db: AsyncSession = Depends(get_db)):
    # Verificar pago existe y estado='A'
    pago = await db.get(Pago, payload.pago_id)
    if not pago:
        raise HTTPException(400, "pago_id inválido")
    if pago.estado != "A":
        raise HTTPException(409, "No se puede crear una liquidación en un pago cerrado")

    # Buscar período cerrado
    periodo_stmt = select(Periodos).where(
        Periodos.MES == payload.mes_periodo,
        Periodos.ANIO == payload.anio_periodo,
        Periodos.NRO_OBRA_SOCIAL == payload.obra_social_id,
        Periodos.CERRADO == "C",
    ).limit(1)
    res = await db.execute(periodo_stmt)
    periodo_obj = res.scalars().first()
    if not periodo_obj:
        raise HTTPException(400, "Periodo inválido o no cerrado")

    nro_factura = f"{periodo_obj.NRO_FACT_1}-{periodo_obj.NRO_FACT_2}"

    # Verificar unicidad
    existing = (await db.execute(
        select(Liquidacion.id).where(
            Liquidacion.pago_id == payload.pago_id,
            Liquidacion.obra_social_id == payload.obra_social_id,
            Liquidacion.mes_periodo == payload.mes_periodo,
            Liquidacion.anio_periodo == payload.anio_periodo,
        ).limit(1)
    )).first()
    if existing:
        raise HTTPException(409, "Ya existe una liquidación con esos datos en el pago")

    liq = Liquidacion(
        pago_id=payload.pago_id,
        obra_social_id=payload.obra_social_id,
        mes_periodo=payload.mes_periodo,
        anio_periodo=payload.anio_periodo,
        nro_factura=nro_factura,
        total_bruto=Decimal("0"),
        total_debitos=Decimal("0"),
        total_creditos=Decimal("0"),
        total_neto=Decimal("0"),
    )
    db.add(liq)
    await db.flush()

    await build_detalles_liquidacion(db, liq.id)
    await recalcular_totales_de_liquidacion(db, liq.id)

    await db.commit()
    await db.refresh(liq)
    return liq


# ================================================
# GET: Obtiene una liquidación por su ID
# ================================================
@router.get("/liquidaciones_por_os/{liquidacion_id}", response_model=LiquidacionRead)
async def obtener_liquidacion(liquidacion_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Liquidacion).where(Liquidacion.id == liquidacion_id))
    obj = res.scalars().first()
    if not obj:
        raise HTTPException(404, "Liquidacion no encontrada")
    return obj


# ================================================
# DELETE: Elimina una liquidación
# ================================================
@router.delete("/liquidaciones_por_os/{liquidacion_id}", status_code=204)
async def eliminar_liquidacion(liquidacion_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Liquidacion).where(Liquidacion.id == liquidacion_id))
    obj = res.scalars().first()
    if not obj:
        raise HTTPException(404, "Liquidacion no encontrada")

    # 409 si pago cerrado
    pago = await db.get(Pago, obj.pago_id)
    if pago and pago.estado == "C":
        raise HTTPException(409, "No se puede eliminar una liquidación de un pago cerrado")

    # 409 si hay lotes en estado 'L' para esta OS+período en el mismo pago
    lote_activo = (await db.execute(
        select(LoteAjuste.id).where(
            LoteAjuste.pago_id == obj.pago_id,
            LoteAjuste.obra_social_id == obj.obra_social_id,
            LoteAjuste.mes_periodo == obj.mes_periodo,
            LoteAjuste.anio_periodo == obj.anio_periodo,
            LoteAjuste.estado == "L",
        ).limit(1)
    )).first()
    if lote_activo:
        raise HTTPException(
            409,
            "Hay un lote de ajustes en estado 'En liquidaciones' para esta factura. "
            "Quitá el lote del pago antes de eliminar la liquidación."
        )

    await db.delete(obj)
    await db.commit()
    return None


# ================================================
# GET: Vista enriquecida de detalles
# ================================================
@router.get(
    "/liquidaciones_por_os/{liquidacion_id}/detalles_vista",
    response_model=list[DetalleVistaRow],
)
async def detalles_vista(
    liquidacion_id: int,
    medico_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None, description="Busca por NRO_SOCIO, NOMBRE o CODIGO_PRESTACION"),
    db: AsyncSession = Depends(get_db),
    response: Response = None,
):
    items, total = await vista_detalles_liquidacion(
        db=db,
        liquidacion_id=liquidacion_id,
        medico_id=medico_id,
        search=search,
    )

    if response is not None:
        response.headers["X-Total-Count"] = str(total)
        response.headers["Content-Range"] = f"items 0-{max(total-1,0)}/{total}"

    return items


# ================================================
# GET: Detalles brutos de una liquidación
# ================================================
@router.get(
    "/liquidaciones_por_os/{liquidacion_id}/detalles",
    response_model=List[DetalleLiquidacionRead],
)
async def listar_detalles_bruto_liquidacion(
    liquidacion_id: int,
    medico_id: Optional[int] = Query(None),
    obra_social_id: Optional[int] = Query(None),
    prestacion_id: Optional[int] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
):
    exists = await db.execute(select(Liquidacion.id).where(Liquidacion.id == liquidacion_id))
    if not exists.first():
        raise HTTPException(404, "Liquidación no encontrada")

    stmt = select(DetalleLiquidacion).where(DetalleLiquidacion.liquidacion_id == liquidacion_id)
    if medico_id is not None:
        stmt = stmt.where(DetalleLiquidacion.medico_id == medico_id)
    if obra_social_id is not None:
        stmt = stmt.where(DetalleLiquidacion.obra_social_id == obra_social_id)
    if prestacion_id is not None:
        stmt = stmt.where(DetalleLiquidacion.prestacion_id == prestacion_id)
    stmt = stmt.order_by(DetalleLiquidacion.id).offset(skip).limit(limit)

    res = await db.execute(stmt)
    return res.scalars().all()


# ================================================
# GET: Obtiene un recibo por ID
# ================================================
@router.get("/recibos/{recibo_id}", response_model=ReciboRead)
async def obtener_recibo(recibo_id: int, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(Recibo).where(Recibo.id == recibo_id))).scalars().first()
    if not row:
        raise HTTPException(404, "Recibo no encontrado")
    return row


# ================================================
# GET: Detalle completo de un recibo
# ================================================
@router.get("/recibos/{recibo_id}/detalle")
async def detalle_recibo_endpoint(recibo_id: int, db: AsyncSession = Depends(get_db)):
    recibo = (await db.execute(select(Recibo).where(Recibo.id == recibo_id))).scalars().first()
    if not recibo:
        raise HTTPException(404, "Recibo no encontrado")
    return await detalle_recibo_medico(db, recibo.pago_id, recibo.medico_id, recibo)


# ================================================
# PUT: Anular un recibo
# ================================================
@router.put("/recibos/{recibo_id}/anular", response_model=ReciboRead)
async def anular_recibo(
    recibo_id: int,
    payload: ReciboAnularPayload,
    db: AsyncSession = Depends(get_db),
):
    """Solo se puede anular un recibo en estado 'emitido'."""
    recibo = (await db.execute(select(Recibo).where(Recibo.id == recibo_id))).scalars().first()
    if not recibo:
        raise HTTPException(404, "Recibo no encontrado")
    if recibo.estado == "anulado":
        raise HTTPException(409, "El recibo ya está anulado")
    if recibo.estado == "pagado":
        raise HTTPException(409, "No se puede anular un recibo ya pagado")

    recibo.estado = "anulado"
    await db.commit()
    await db.refresh(recibo)
    return recibo
