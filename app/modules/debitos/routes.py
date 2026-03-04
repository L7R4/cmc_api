from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import Debito_Credito, DetalleLiquidacion, GuardarAtencion, Liquidacion
from app.modules.debitos.schemas import (
    DebCreByDetalleIn,
    DebCreByDetalleOut,
    DebCreByDetalleRecalcOut,
    DebCreResumenOut,
    DebCreRowOut,
    DebCreListOut,
    DebCreItemOut,
)
from app.modules.liquidacion.service import _calc_row_total, _dec, recalcular_totales_de_liquidacion

router = APIRouter()


def _build_resumen_out(liq: Liquidacion) -> DebCreResumenOut:
    return DebCreResumenOut(
        liquidacion_id=liq.id,
        nro_factura=liq.nro_factura,
        total_bruto=_dec(liq.total_bruto),
        total_debitos=_dec(liq.total_debitos),
        total_neto=_dec(liq.total_neto),
    )


async def _get_det_and_liq(
    db: AsyncSession, detalle_id: int
) -> tuple[DetalleLiquidacion, Liquidacion]:
    det = await db.get(DetalleLiquidacion, detalle_id)
    if not det:
        raise HTTPException(404, "Detalle de liquidación no encontrado")
    liq = await db.get(Liquidacion, det.liquidacion_id)
    if not liq:
        raise HTTPException(404, "Liquidación no encontrada")
    if liq.estado != "A":
        raise HTTPException(409, "La liquidación está cerrada")
    return det, liq


async def _sum_debitos_existentes(db: AsyncSession, det_id: int) -> Decimal:
    """Suma total de débitos activos para un detalle (para validar límite)."""
    res = await db.execute(
        select(func.coalesce(func.sum(Debito_Credito.monto), 0))
        .where(
            Debito_Credito.detalle_liquidacion_id == det_id,
            Debito_Credito.tipo == "d",
        )
    )
    return Decimal(str(res.scalar_one() or 0))


# ================================================
# POST: Agregar un nuevo DC a un detalle
# AGENT DO IT: Ahora crea un NUEVO DC (N DCs por detalle)
# ================================================
@router.post("/by_detalle/{detalle_id}", response_model=DebCreByDetalleRecalcOut, status_code=201)
async def agregar_dc_a_detalle(
    detalle_id: int,
    payload: DebCreByDetalleIn,
    db: AsyncSession = Depends(get_db),
):
    """
    Agrega un nuevo Débito/Crédito al detalle indicado.
    Un detalle puede tener N DCs (múltiples débitos y/o créditos).

    Regla: si tipo='d' y el total de débitos supera el importe de la prestación → 422.
    """
    det, liq = await _get_det_and_liq(db, detalle_id)

    tipo_in = (payload.tipo or "").lower()
    if tipo_in not in ("d", "c"):
        raise HTTPException(422, "tipo debe ser 'd' (débito) o 'c' (crédito)")

    if payload.monto <= 0:
        raise HTTPException(422, "monto debe ser mayor a cero")

    # Validar: débito no puede superar el importe de la prestación
    if tipo_in == "d":
        sum_debs = await _sum_debitos_existentes(db, detalle_id)
        if sum_debs + payload.monto > Decimal(str(det.importe or 0)):
            raise HTTPException(
                422,
                f"El débito supera el importe de la prestación. "
                f"Importe: {det.importe}, Débitos existentes: {sum_debs}, Nuevo débito: {payload.monto}",
            )

    # Verificar que la atención existe
    atencion = await db.get(GuardarAtencion, det.prestacion_id)
    if not atencion:
        raise HTTPException(404, "Atención (GuardarAtencion) inexistente para este detalle")

    dc = Debito_Credito(
        tipo=tipo_in,
        id_atencion=det.prestacion_id,
        obra_social_id=liq.obra_social_id,
        anio=liq.anio_periodo,
        mes=liq.mes_periodo,
        monto=payload.monto,
        observacion=payload.observacion,
        detalle_liquidacion_id=det.id,
        created_by_user=payload.created_by_user,
    )
    db.add(dc)
    await db.flush()

    await recalcular_totales_de_liquidacion(db, liq.id)
    await db.commit()
    await db.refresh(liq)

    row_total = await _calc_row_total(db, det, liq)
    tipo_ui = tipo_in.upper()

    return DebCreByDetalleRecalcOut(
        det_id=det.id,
        dc_id=dc.id,
        row=DebCreRowOut(
            det_id=det.id,
            dc_id=dc.id,
            tipo=tipo_ui,
            monto=_dec(dc.monto),
            obs=dc.observacion,
            importe=_dec(det.importe),
            pagado=_dec(det.pagado),
            total=row_total,
        ),
        resumen=_build_resumen_out(liq),
    )


# ================================================
# GET: Listar todos los DCs de un detalle
# ================================================
@router.get("/by_detalle/{detalle_id}", response_model=DebCreListOut)
async def listar_dcs_de_detalle(
    detalle_id: int,
    db: AsyncSession = Depends(get_db),
):
    det = await db.get(DetalleLiquidacion, detalle_id)
    if not det:
        raise HTTPException(404, "Detalle de liquidación no encontrado")
    liq = await db.get(Liquidacion, det.liquidacion_id)
    if not liq:
        raise HTTPException(404, "Liquidación no encontrada")

    dcs = (await db.execute(
        select(Debito_Credito)
        .where(Debito_Credito.detalle_liquidacion_id == detalle_id)
        .order_by(Debito_Credito.id)
    )).scalars().all()

    items = [
        DebCreItemOut(
            dc_id=dc.id,
            tipo=dc.tipo.upper(),
            monto=_dec(dc.monto),
            obs=dc.observacion,
        )
        for dc in dcs
    ]
    row_total = await _calc_row_total(db, det, liq)
    return DebCreListOut(
        det_id=det.id,
        importe=_dec(det.importe),
        pagado=_dec(det.pagado),
        total=row_total,
        items=items,
    )


# ================================================
# PUT: Actualizar un DC específico
# ================================================
@router.put("/dc/{dc_id}", response_model=DebCreByDetalleRecalcOut)
async def actualizar_dc(
    dc_id: int,
    payload: DebCreByDetalleIn,
    db: AsyncSession = Depends(get_db),
):
    dc = await db.get(Debito_Credito, dc_id)
    if not dc:
        raise HTTPException(404, "Débito/Crédito no encontrado")

    det, liq = await _get_det_and_liq(db, dc.detalle_liquidacion_id)

    tipo_in = (payload.tipo or "").lower()
    if tipo_in not in ("d", "c"):
        raise HTTPException(422, "tipo debe ser 'd' o 'c'")
    if payload.monto <= 0:
        raise HTTPException(422, "monto debe ser mayor a cero")

    # Validar límite de débito (excluyendo el DC actual del sum)
    if tipo_in == "d":
        sum_otros_debs = (await db.execute(
            select(func.coalesce(func.sum(Debito_Credito.monto), 0))
            .where(
                Debito_Credito.detalle_liquidacion_id == det.id,
                Debito_Credito.tipo == "d",
                Debito_Credito.id != dc_id,
            )
        )).scalar_one()
        sum_otros_debs = Decimal(str(sum_otros_debs or 0))
        if sum_otros_debs + payload.monto > Decimal(str(det.importe or 0)):
            raise HTTPException(
                422,
                f"El débito total supera el importe de la prestación ({det.importe}).",
            )

    dc.tipo = tipo_in
    dc.monto = payload.monto
    dc.observacion = payload.observacion

    await recalcular_totales_de_liquidacion(db, liq.id)
    await db.commit()
    await db.refresh(liq)

    row_total = await _calc_row_total(db, det, liq)

    return DebCreByDetalleRecalcOut(
        det_id=det.id,
        dc_id=dc.id,
        row=DebCreRowOut(
            det_id=det.id,
            dc_id=dc.id,
            tipo=tipo_in.upper(),
            monto=_dec(dc.monto),
            obs=dc.observacion,
            importe=_dec(det.importe),
            pagado=_dec(det.pagado),
            total=row_total,
        ),
        resumen=_build_resumen_out(liq),
    )


# ================================================
# DELETE: Eliminar un DC específico
# ================================================
@router.delete("/dc/{dc_id}", response_model=DebCreByDetalleRecalcOut)
async def eliminar_dc(dc_id: int, db: AsyncSession = Depends(get_db)):
    dc = await db.get(Debito_Credito, dc_id)
    if not dc:
        raise HTTPException(404, "Débito/Crédito no encontrado")

    det, liq = await _get_det_and_liq(db, dc.detalle_liquidacion_id)

    await db.delete(dc)
    await db.flush()

    await recalcular_totales_de_liquidacion(db, liq.id)
    await db.commit()
    await db.refresh(liq)

    row_total = await _calc_row_total(db, det, liq)

    return DebCreByDetalleRecalcOut(
        det_id=det.id,
        dc_id=None,
        row=DebCreRowOut(
            det_id=det.id,
            dc_id=None,
            tipo="N",
            monto=0.0,
            obs=None,
            importe=_dec(det.importe),
            pagado=_dec(det.pagado),
            total=row_total,
        ),
        resumen=_build_resumen_out(liq),
    )
