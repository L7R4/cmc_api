from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Literal
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.database import get_db
from app.db.models import DetalleLiquidacion, Debito_Credito, GuardarAtencion, Liquidacion

from app.schemas.debitos_creditos_schema import DebCreByDetalleIn, DebCreByDetalleOut, DebCreByDetalleRecalcOut, DebCreResumenOut, DebCreRowOut
from app.services.liquidaciones import recomputar_totales_de_liquidacion, recomputar_totales_de_resumen
from app.services.liquidaciones_calc import _calc_row_total, _dec
router_dc = APIRouter()


def _parse_atencion_id(prestacion_id: str | int) -> int:
    # robusto: " 37591 " -> 37591 ; "37591-XYZ" -> 37591 si sólo hay dígitos al inicio
    s = str(prestacion_id).strip()
    # intenta cast directo
    try:
        return int(s)
    except ValueError:
        # extrae dígitos consecutivos al principio
        digits = []
        for ch in s:
            if ch.isdigit(): digits.append(ch)
            else: break
        if not digits:
            raise HTTPException(400, "prestacion_id no es numérico")
        return int("".join(digits))

@router_dc.post("/by_detalle/{detalle_id}", response_model=DebCreByDetalleRecalcOut)
async def upsert_by_detalle(
    detalle_id: int,
    payload: DebCreByDetalleIn,
    db: AsyncSession = Depends(get_db),
):
    det = await db.get(DetalleLiquidacion, detalle_id)
    if not det:
        raise HTTPException(404, "Detalle de liquidacion no encontrada")
    liq = await db.get(Liquidacion, det.liquidacion_id)
    if not liq:
        raise HTTPException(404, "Liquidación no encontrada")
    if liq.estado != "A":
        raise HTTPException(409, "La liquidación está cerrada")

    tipo_in = (payload.tipo or "").lower()

    # Quitar DC
    if tipo_in == "n" or payload.monto <= 0:
        if det.debito_credito_id:
            dc = await db.get(Debito_Credito, det.debito_credito_id)
            if dc:
                await db.delete(dc)
        det.debito_credito_id = None
        await recomputar_totales_de_liquidacion(db, liq.id)
        await recomputar_totales_de_resumen(db,liq.resumen_id)
        
        await db.commit()

        row_total = await _calc_row_total(db, det, liq)
        return DebCreByDetalleRecalcOut(
            det_id=det.id,
            debito_credito_id=None,
            row=DebCreRowOut(
                det_id=det.id,
                tipo="N",
                monto=0.0,
                obs=None,
                importe=_dec(det.importe),
                pagado=_dec(det.pagado),
                total=row_total,
            ),
            resumen=DebCreResumenOut(
                liquidacion_id=liq.id,
                nro_liquidacion=liq.nro_liquidacion,
                total_bruto=_dec(liq.total_bruto),
                total_debitos=_dec(liq.total_debitos),
                total_neto=_dec(liq.total_neto),
            ),
        )

    # Upsert DC (igual a lo que ya tenías) --------------------
    atencion_id = _parse_atencion_id(det.prestacion_id)
    guardar_atencion_item = await db.get(GuardarAtencion, atencion_id)
    if not guardar_atencion_item:
        raise HTTPException(404, "Atención (GuardarAtencion) inexistente")

    if det.debito_credito_id:
        dc = await db.get(Debito_Credito, det.debito_credito_id)
        if not dc:
            dc = Debito_Credito(
                tipo=tipo_in,
                id_atencion=guardar_atencion_item.ID,
                obra_social_id=liq.obra_social_id,
                periodo=f"{liq.anio_periodo:04d}-{liq.mes_periodo:02d}",
                monto=payload.monto,
                observacion=payload.observacion,
                created_by_user=payload.created_by_user,
            )
            db.add(dc)
            await db.flush()
            det.debito_credito_id = dc.id
        else:
            dc.tipo = tipo_in
            dc.monto = payload.monto
            dc.observacion = payload.observacion
            dc.obra_social_id = liq.obra_social_id
            dc.periodo = f"{liq.anio_periodo:04d}-{liq.mes_periodo:02d}"
            dc.id_atencion = guardar_atencion_item.ID
            await db.flush()
    else:
        dc = Debito_Credito(
            tipo=tipo_in,
            id_atencion=guardar_atencion_item.ID,
            obra_social_id=liq.obra_social_id,
            periodo=f"{liq.anio_periodo:04d}-{liq.mes_periodo:02d}",
            monto=payload.monto,
            observacion=payload.observacion,
            created_by_user=payload.created_by_user,
        )
        db.add(dc)
        await db.flush()
        det.debito_credito_id = dc.id

    await recomputar_totales_de_liquidacion(db, liq.id)
    await recomputar_totales_de_resumen(db,liq.resumen_id)
    

    await db.commit()

    row_total = await _calc_row_total(db, det, liq)
    tipo_ui = (dc.tipo or "n").upper() if det.debito_credito_id else "N"

    return DebCreByDetalleRecalcOut(
        det_id=det.id,
        debito_credito_id=det.debito_credito_id,
        row=DebCreRowOut(
            det_id=det.id,
            tipo=tipo_ui,
            monto=_dec(dc.monto) if det.debito_credito_id else 0.0,
            obs=(dc.observacion if det.debito_credito_id else None),
            importe=_dec(det.importe),
            pagado=_dec(det.pagado),
            total=row_total,
        ),
        resumen=DebCreResumenOut(
            liquidacion_id=liq.id,
            nro_liquidacion=liq.nro_liquidacion,
            total_bruto=_dec(liq.total_bruto),
            total_debitos=_dec(liq.total_debitos),
            total_neto=_dec(liq.total_neto),
        ),
    )


@router_dc.delete("/by_detalle/{detalle_id}", response_model=DebCreByDetalleRecalcOut)
async def delete_by_detalle(detalle_id: int, db: AsyncSession = Depends(get_db)):
    det = await db.get(DetalleLiquidacion, detalle_id)
    if not det:
        raise HTTPException(404, "Detalle de liquidacion no encontrada")

    liq = await db.get(Liquidacion, det.liquidacion_id)
    if not liq:
        raise HTTPException(404, "Liquidación no encontrada")

    if liq.estado != "A":
        raise HTTPException(409, "La liquidación está cerrada")

    if det.debito_credito_id:
        dc = await db.get(Debito_Credito, det.debito_credito_id)
        if dc:
            await db.delete(dc)
        det.debito_credito_id = None

    await recomputar_totales_de_liquidacion(db, liq.id)
    await recomputar_totales_de_resumen(db,liq.resumen_id)

    await db.commit()

    row_total = await _calc_row_total(db, det, liq)
    return DebCreByDetalleRecalcOut(
        det_id=det.id,
        debito_credito_id=None,
        row=DebCreRowOut(
            det_id=det.id,
            tipo="N",
            monto=0.0,
            obs=None,
            importe=_dec(det.importe),
            pagado=_dec(det.pagado),
            total=row_total,
        ),
        resumen=DebCreResumenOut(
            liquidacion_id=liq.id,
            nro_liquidacion=liq.nro_liquidacion,
            total_bruto=_dec(liq.total_bruto),
            total_debitos=_dec(liq.total_debitos),
            total_neto=_dec(liq.total_neto),
        ),
    )
# Alias: POST hace lo mismo que PUT (upsert)
# @router_dc.post("/by_detalle/{detalle_id}", response_model=DebCreByDetalleOut, status_code=201)
# async def create_by_detalle(
#     detalle_id: int,
#     payload: DebCreByDetalleIn,
#     db: AsyncSession = Depends(get_db),
# ):
#     return await upsert_by_detalle(detalle_id, payload, db)

