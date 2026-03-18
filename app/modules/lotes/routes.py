from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.database import get_db
from app.db.models import (
    Ajuste,
    LoteAjuste,
    Pago,
)
from app.modules.lotes.schemas import (
    AjusteCreate,
    AjusteRead,
    AjusteUpdate,
    LoteAjusteCreate,
    LoteAjusteRead,
    LoteRefacturacionCreate,
)

router = APIRouter()


# ================================================
# Helper: recalcular totales del lote
# ================================================
async def recalcular_totales_lote(db: AsyncSession, lote_id: int) -> None:
    res = await db.execute(
        select(
            func.coalesce(func.sum(case((Ajuste.tipo == "d", Ajuste.monto), else_=0)), 0).label("debitos"),
            func.coalesce(func.sum(case((Ajuste.tipo == "c", Ajuste.monto), else_=0)), 0).label("creditos"),
        ).where(Ajuste.lote_id == lote_id)
    )
    row = res.first()
    lote = await db.get(LoteAjuste, lote_id)
    if lote:
        lote.total_debitos = Decimal(str(row.debitos or 0))
        lote.total_creditos = Decimal(str(row.creditos or 0))
    await db.flush()


def _lote_with_ajustes(lote: LoteAjuste) -> LoteAjusteRead:
    ajustes = [AjusteRead.model_validate(a) for a in (lote.ajustes or [])]
    return LoteAjusteRead(
        id=lote.id,
        obra_social_id=lote.obra_social_id,
        mes_periodo=lote.mes_periodo,
        anio_periodo=lote.anio_periodo,
        tipo=lote.tipo,
        snap_origen_id=lote.snap_origen_id,
        estado=lote.estado,
        pago_id=lote.pago_id,
        total_debitos=lote.total_debitos,
        total_creditos=lote.total_creditos,
        ajustes=ajustes,
    )


async def _get_lote_with_ajustes(db: AsyncSession, lote_id: int) -> LoteAjuste:
    stmt = (
        select(LoteAjuste)
        .options(selectinload(LoteAjuste.ajustes))
        .where(LoteAjuste.id == lote_id)
    )
    lote = (await db.execute(stmt)).scalars().first()
    if not lote:
        raise HTTPException(404, "Lote no encontrado")
    return lote


# ================================================
# POST /snaps/obtener_o_crear
# ================================================
@router.post("/snaps/obtener_o_crear", response_model=LoteAjusteRead)
async def obtener_o_crear_lote(
    payload: LoteAjusteCreate,
    db: AsyncSession = Depends(get_db),
):
    """Busca lote tipo='normal' para ese OS+período; si no existe, crea uno en estado='A'."""
    stmt = (
        select(LoteAjuste)
        .options(selectinload(LoteAjuste.ajustes))
        .where(
            LoteAjuste.obra_social_id == payload.obra_social_id,
            LoteAjuste.mes_periodo == payload.mes_periodo,
            LoteAjuste.anio_periodo == payload.anio_periodo,
            LoteAjuste.tipo == "normal",
        )
        .limit(1)
    )
    lote = (await db.execute(stmt)).scalars().first()

    if not lote:
        lote = LoteAjuste(
            obra_social_id=payload.obra_social_id,
            mes_periodo=payload.mes_periodo,
            anio_periodo=payload.anio_periodo,
            tipo="normal",
            estado="A",
            total_debitos=Decimal("0"),
            total_creditos=Decimal("0"),
        )
        db.add(lote)
        await db.commit()
        await db.refresh(lote)

    return _lote_with_ajustes(lote)


# ================================================
# POST /snaps/crear_refacturacion
# ================================================
@router.post("/snaps/crear_refacturacion", response_model=LoteAjusteRead, status_code=201)
async def crear_lote_refacturacion(
    payload: LoteRefacturacionCreate,
    db: AsyncSession = Depends(get_db),
):
    """Siempre crea un nuevo lote tipo='refacturacion'."""
    lote = LoteAjuste(
        obra_social_id=payload.obra_social_id,
        mes_periodo=payload.mes_periodo,
        anio_periodo=payload.anio_periodo,
        tipo="refacturacion",
        snap_origen_id=payload.snap_origen_id,
        estado="A",
        total_debitos=Decimal("0"),
        total_creditos=Decimal("0"),
    )
    db.add(lote)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Ya existe un lote de refacturación para ese lote origen (uq_lote_origen)")

    await db.commit()
    await db.refresh(lote)
    return _lote_with_ajustes(lote)


# ================================================
# GET /snaps/por_os_periodo — Lista lotes por OS+período
# ================================================
# IMPORTANT: must be defined BEFORE /snaps/{lote_id} to avoid capture
@router.get("/snaps/por_os_periodo", response_model=List[LoteAjusteRead])
async def listar_lotes_por_os_periodo(
    obra_social_id: int = Query(...),
    mes_periodo: int = Query(..., ge=1, le=12),
    anio_periodo: int = Query(..., ge=1900, le=3000),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(LoteAjuste)
        .options(selectinload(LoteAjuste.ajustes))
        .where(
            LoteAjuste.obra_social_id == obra_social_id,
            LoteAjuste.mes_periodo == mes_periodo,
            LoteAjuste.anio_periodo == anio_periodo,
        )
        .order_by(LoteAjuste.created_at)
    )
    lotes = (await db.execute(stmt)).scalars().all()
    return [_lote_with_ajustes(l) for l in lotes]


# ================================================
# GET /snaps/{lote_id} — Detalle del lote
# ================================================
@router.get("/snaps/{lote_id}", response_model=LoteAjusteRead)
async def obtener_lote(lote_id: int, db: AsyncSession = Depends(get_db)):
    lote = await _get_lote_with_ajustes(db, lote_id)
    return _lote_with_ajustes(lote)


# ================================================
# POST /snaps/{lote_id}/cerrar
# ================================================
@router.post("/snaps/{lote_id}/cerrar", response_model=LoteAjusteRead)
async def cerrar_lote(lote_id: int, db: AsyncSession = Depends(get_db)):
    lote = await _get_lote_with_ajustes(db, lote_id)
    if lote.estado in ("C", "L"):
        raise HTTPException(409, f"No se puede cerrar un lote en estado '{lote.estado}'")
    lote.estado = "C"
    await db.commit()
    return _lote_with_ajustes(await _get_lote_with_ajustes(db, lote_id))


# ================================================
# POST /snaps/{lote_id}/reabrir
# ================================================
@router.post("/snaps/{lote_id}/reabrir", response_model=LoteAjusteRead)
async def reabrir_lote(lote_id: int, db: AsyncSession = Depends(get_db)):
    lote = await _get_lote_with_ajustes(db, lote_id)
    if lote.estado == "L":
        raise HTTPException(409, "No se puede reabrir un lote que está en un pago (estado='L')")
    if lote.estado == "A":
        raise HTTPException(409, "El lote ya está abierto")
    lote.estado = "A"
    await db.commit()
    return _lote_with_ajustes(await _get_lote_with_ajustes(db, lote_id))


# ================================================
# POST /snaps/{lote_id}/en_liquidaciones
# ================================================
@router.post("/snaps/{lote_id}/en_liquidaciones", response_model=LoteAjusteRead)
async def pasar_lote_a_pago(lote_id: int, db: AsyncSession = Depends(get_db)):
    """Asigna el lote al pago activo (estado='A')."""
    lote = await _get_lote_with_ajustes(db, lote_id)

    if lote.estado != "C":
        raise HTTPException(409, "El lote debe estar cerrado (estado='C') para pasar a liquidaciones")
    if lote.pago_id is not None:
        raise HTTPException(409, "El lote ya está asignado a un pago")

    # Buscar el único pago abierto
    pago_abierto = (await db.execute(
        select(Pago).where(Pago.estado == "A").limit(1)
    )).scalars().first()
    if not pago_abierto:
        raise HTTPException(409, "No hay pago abierto al que asignar el lote")

    lote.pago_id = pago_abierto.id
    lote.estado = "L"
    await db.commit()
    return _lote_with_ajustes(await _get_lote_with_ajustes(db, lote_id))


# ================================================
# DELETE /pagos/{pago_id}/snaps/{lote_id}
# ================================================
@router.delete("/pagos/{pago_id}/snaps/{lote_id}", response_model=LoteAjusteRead)
async def quitar_lote_de_pago(
    pago_id: int,
    lote_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Desvincula el lote del pago. 409 si pago cerrado."""
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")
    if pago.estado == "C":
        raise HTTPException(409, "No se puede quitar un lote de un pago cerrado")

    lote = await _get_lote_with_ajustes(db, lote_id)
    if lote.pago_id != pago_id:
        raise HTTPException(409, "El lote no pertenece a ese pago")

    lote.pago_id = None
    lote.estado = "C"
    await db.commit()
    return _lote_with_ajustes(await _get_lote_with_ajustes(db, lote_id))


# ================================================
# POST /snaps/{lote_id}/items — Crear ajuste
# ================================================
@router.post("/snaps/{lote_id}/items", response_model=AjusteRead, status_code=201)
async def crear_ajuste(
    lote_id: int,
    payload: AjusteCreate,
    db: AsyncSession = Depends(get_db),
):
    lote = await db.get(LoteAjuste, lote_id)
    if not lote:
        raise HTTPException(404, "Lote no encontrado")
    if lote.estado != "A":
        raise HTTPException(409, f"No se puede agregar ajustes a un lote en estado '{lote.estado}'")

    ajuste = Ajuste(
        lote_id=lote_id,
        tipo=payload.tipo,
        medico_id=payload.medico_id,
        obra_social_id=lote.obra_social_id,
        monto=payload.monto,
        observacion=payload.observacion,
        id_atencion=payload.id_atencion,
        origen="manual",
    )
    db.add(ajuste)
    await db.flush()
    await recalcular_totales_lote(db, lote_id)
    await db.commit()
    await db.refresh(ajuste)
    return ajuste


# ================================================
# PUT /snaps/{lote_id}/items/{ajuste_id} — Actualizar ajuste
# ================================================
@router.put("/snaps/{lote_id}/items/{ajuste_id}", response_model=AjusteRead)
async def actualizar_ajuste(
    lote_id: int,
    ajuste_id: int,
    payload: AjusteUpdate,
    db: AsyncSession = Depends(get_db),
):
    lote = await db.get(LoteAjuste, lote_id)
    if not lote:
        raise HTTPException(404, "Lote no encontrado")
    if lote.estado != "A":
        raise HTTPException(409, f"No se puede editar ajustes en un lote en estado '{lote.estado}'")

    ajuste = (await db.execute(
        select(Ajuste).where(Ajuste.id == ajuste_id, Ajuste.lote_id == lote_id)
    )).scalars().first()
    if not ajuste:
        raise HTTPException(404, "Ajuste no encontrado en ese lote")

    if payload.tipo is not None:
        ajuste.tipo = payload.tipo
    if payload.monto is not None:
        ajuste.monto = payload.monto
    if payload.observacion is not None:
        ajuste.observacion = payload.observacion

    await db.flush()
    await recalcular_totales_lote(db, lote_id)
    await db.commit()
    await db.refresh(ajuste)
    return ajuste


# ================================================
# DELETE /snaps/{lote_id}/items/{ajuste_id} — Eliminar ajuste
# ================================================
@router.delete("/snaps/{lote_id}/items/{ajuste_id}", status_code=204)
async def eliminar_ajuste(
    lote_id: int,
    ajuste_id: int,
    db: AsyncSession = Depends(get_db),
):
    lote = await db.get(LoteAjuste, lote_id)
    if not lote:
        raise HTTPException(404, "Lote no encontrado")
    if lote.estado != "A":
        raise HTTPException(409, f"No se puede eliminar ajustes de un lote en estado '{lote.estado}'")

    ajuste = (await db.execute(
        select(Ajuste).where(Ajuste.id == ajuste_id, Ajuste.lote_id == lote_id)
    )).scalars().first()
    if not ajuste:
        raise HTTPException(404, "Ajuste no encontrado en ese lote")

    await db.delete(ajuste)
    await db.flush()
    await recalcular_totales_lote(db, lote_id)
    await db.commit()
    return None
