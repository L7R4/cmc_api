import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import (
    Liquidacion,
    Pago,
    PagoMedico,
    Recibo,
)
from app.modules.pagos.schemas import (
    PagoCreate,
    PagoMedicoListResponse,
    PagoMedicoRead,
    PagoMedicoTotales,
    PagoRead,
    PagoUpdate,
)
from app.modules.pagos.service import (
    emitir_recibos,
    generar_pago_medico,
    recalcular_totales_pago,
)

router = APIRouter()


def _enrich_pago(pago: Pago, totales: dict) -> PagoRead:
    return PagoRead(
        id=pago.id,
        anio=pago.anio,
        mes=pago.mes,
        descripcion=pago.descripcion,
        estado=pago.estado,
        cierre_timestamp=pago.cierre_timestamp,
        total_bruto=totales["total_bruto"],
        total_debitos=totales["total_debitos"],
        total_creditos=totales["total_creditos"],
        total_neto=totales["total_neto"],
        total_deduccion=totales["total_deduccion"],
    )


# ================================================
# GET /pagos — Listar pagos
# ================================================
@router.get("/", response_model=List[PagoRead])
async def listar_pagos(
    anio: Optional[int] = Query(None, ge=1900, le=3000),
    mes: Optional[int] = Query(None, ge=1, le=12),
    estado: Optional[str] = Query(None, description="A o C"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Pago).order_by(Pago.anio.desc(), Pago.mes.desc(), Pago.id.desc())
    if anio is not None:
        stmt = stmt.where(Pago.anio == anio)
    if mes is not None:
        stmt = stmt.where(Pago.mes == mes)
    if estado is not None:
        stmt = stmt.where(Pago.estado == estado)
    stmt = stmt.offset(skip).limit(limit)

    pagos = (await db.execute(stmt)).scalars().all()

    result = []
    for pago in pagos:
        totales = await recalcular_totales_pago(db, pago.id)
        result.append(_enrich_pago(pago, totales))
    return result


# ================================================
# POST /pagos — Crear pago
# ================================================
@router.post("/", response_model=PagoRead, status_code=201)
async def crear_pago(payload: PagoCreate, db: AsyncSession = Depends(get_db)):
    # 409 si ya hay pago en estado='A'
    existing_open = (await db.execute(
        select(Pago.id).where(Pago.estado == "A").limit(1)
    )).first()
    if existing_open:
        raise HTTPException(
            409,
            detail={
                "reason": "pago_abierto_existe",
                "pago_id": existing_open[0],
                "message": "Ya existe un pago en estado abierto. Ciérrelo antes de crear uno nuevo.",
            },
        )

    obj = Pago(anio=payload.anio, mes=payload.mes, descripcion=payload.descripcion)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)

    totales = await recalcular_totales_pago(db, obj.id)
    return _enrich_pago(obj, totales)


# ================================================
# GET /pagos/{pago_id} — Obtener pago
# ================================================
@router.get("/{pago_id}", response_model=PagoRead)
async def obtener_pago(pago_id: int, db: AsyncSession = Depends(get_db)):
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")
    totales = await recalcular_totales_pago(db, pago_id)
    return _enrich_pago(pago, totales)


# ================================================
# PUT /pagos/{pago_id} — Editar pago
# ================================================
@router.put("/{pago_id}", response_model=PagoRead)
async def editar_pago(pago_id: int, payload: PagoUpdate, db: AsyncSession = Depends(get_db)):
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")
    if pago.estado == "C":
        raise HTTPException(409, "No se puede editar un pago cerrado")

    if payload.descripcion is not None:
        pago.descripcion = payload.descripcion

    await db.commit()
    await db.refresh(pago)
    totales = await recalcular_totales_pago(db, pago_id)
    return _enrich_pago(pago, totales)


# ================================================
# DELETE /pagos/{pago_id} — Eliminar pago
# ================================================
@router.delete("/{pago_id}", status_code=204)
async def eliminar_pago(pago_id: int, db: AsyncSession = Depends(get_db)):
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")

    # 409 si tiene liquidaciones
    liq_count = (await db.execute(
        select(func.count(Liquidacion.id)).where(Liquidacion.pago_id == pago_id)
    )).scalar_one()
    if liq_count > 0:
        raise HTTPException(409, "No se puede eliminar un pago con liquidaciones asociadas")

    # 409 si tiene recibos
    rec_count = (await db.execute(
        select(func.count(Recibo.id)).where(Recibo.pago_id == pago_id)
    )).scalar_one()
    if rec_count > 0:
        raise HTTPException(409, "No se puede eliminar un pago con recibos emitidos")

    await db.delete(pago)
    await db.commit()
    return None


# ================================================
# POST /pagos/{pago_id}/cerrar — Cerrar pago
# ================================================
@router.post("/{pago_id}/cerrar", status_code=200)
async def cerrar_pago(pago_id: int, db: AsyncSession = Depends(get_db)):
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")
    if pago.estado == "C":
        raise HTTPException(409, "El pago ya está cerrado")

    pago.estado = "C"
    pago.cierre_timestamp = datetime.datetime.now()
    await db.commit()
    await db.refresh(pago)
    totales = await recalcular_totales_pago(db, pago_id)
    return _enrich_pago(pago, totales)


# ================================================
# POST /pagos/{pago_id}/reabrir — Reabrir pago
# ================================================
@router.post("/{pago_id}/reabrir", response_model=PagoRead)
async def reabrir_pago(pago_id: int, db: AsyncSession = Depends(get_db)):
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")
    if pago.estado == "A":
        raise HTTPException(409, "El pago ya está abierto")

    # 409 si hay recibos emitidos o pagados
    rec_activos = (await db.execute(
        select(func.count(Recibo.id)).where(
            Recibo.pago_id == pago_id,
            Recibo.estado.in_(["emitido", "pagado"]),
        )
    )).scalar_one()
    if rec_activos > 0:
        raise HTTPException(409, "No se puede reabrir un pago con recibos emitidos o pagados")

    # 409 si ya hay otro pago abierto
    otro_abierto = (await db.execute(
        select(Pago.id).where(Pago.estado == "A", Pago.id != pago_id).limit(1)
    )).first()
    if otro_abierto:
        raise HTTPException(409, f"Ya existe otro pago abierto (id={otro_abierto[0]})")

    pago.estado = "A"
    pago.cierre_timestamp = None
    await db.commit()
    await db.refresh(pago)
    totales = await recalcular_totales_pago(db, pago_id)
    return _enrich_pago(pago, totales)


# ================================================
# POST /pagos/{pago_id}/generar_pago_medico
# ================================================
@router.post("/{pago_id}/generar_pago_medico", status_code=200)
async def generar_pago_medico_endpoint(pago_id: int, db: AsyncSession = Depends(get_db)):
    items = await generar_pago_medico(db, pago_id)
    await db.commit()
    return {"pago_id": pago_id, "total_medicos": len(items), "items": items}


# ================================================
# GET /pagos/{pago_id}/pago_medico — Listar PagoMedico
# ================================================
@router.get("/{pago_id}/pago_medico", response_model=PagoMedicoListResponse)
async def listar_pago_medico(
    pago_id: int,
    db: AsyncSession = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
):
    # Totales del dataset completo
    totales_q = await db.execute(
        select(
            func.count(PagoMedico.id).label("total_medicos"),
            func.coalesce(func.sum(PagoMedico.bruto), 0).label("total_bruto"),
            func.coalesce(func.sum(PagoMedico.debitos), 0).label("total_debitos"),
            func.coalesce(func.sum(PagoMedico.creditos), 0).label("total_creditos"),
            func.coalesce(func.sum(PagoMedico.reconocido), 0).label("total_reconocido"),
            func.coalesce(func.sum(PagoMedico.deducciones), 0).label("total_deducciones"),
            func.coalesce(func.sum(PagoMedico.neto_a_pagar), 0).label("total_neto_a_pagar"),
        ).where(PagoMedico.pago_id == pago_id)
    )
    t = totales_q.first()

    # Items paginados
    rows = (await db.execute(
        select(PagoMedico)
        .where(PagoMedico.pago_id == pago_id)
        .order_by(PagoMedico.medico_id)
        .offset(skip)
        .limit(limit)
    )).scalars().all()

    return PagoMedicoListResponse(
        totales=PagoMedicoTotales(
            total_medicos=t.total_medicos or 0,
            total_bruto=t.total_bruto or 0,
            total_debitos=t.total_debitos or 0,
            total_creditos=t.total_creditos or 0,
            total_reconocido=t.total_reconocido or 0,
            total_deducciones=t.total_deducciones or 0,
            total_neto_a_pagar=t.total_neto_a_pagar or 0,
        ),
        items=rows,
    )


# ================================================
# GET /pagos/{pago_id}/pago_medico/{medico_id}
# ================================================
@router.get("/{pago_id}/pago_medico/{medico_id}", response_model=PagoMedicoRead)
async def obtener_pago_medico(pago_id: int, medico_id: int, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(
        select(PagoMedico).where(
            PagoMedico.pago_id == pago_id,
            PagoMedico.medico_id == medico_id,
        )
    )).scalars().first()
    if not row:
        raise HTTPException(404, "No existe pago_medico para ese médico en ese pago")
    return row


# ================================================
# POST /pagos/{pago_id}/emitir_recibos
# ================================================
@router.post("/{pago_id}/emitir_recibos", status_code=200)
async def emitir_recibos_endpoint(pago_id: int, db: AsyncSession = Depends(get_db)):
    items = await emitir_recibos(db, pago_id)
    await db.commit()
    return {"pago_id": pago_id, "total_recibos": len(items), "recibos": items}


# ================================================
# GET /pagos/{pago_id}/recibos — Listar recibos
# ================================================
@router.get("/{pago_id}/recibos")
async def listar_recibos_pago(
    pago_id: int,
    estado: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Recibo).where(Recibo.pago_id == pago_id)
    if estado:
        stmt = stmt.where(Recibo.estado == estado)
    stmt = stmt.order_by(Recibo.medico_id).offset(skip).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return rows
