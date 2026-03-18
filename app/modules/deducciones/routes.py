from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, case, func, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import (
    Ajuste,
    Deduccion,
    DeduccionAplicacion,
    DeduccionSaldo,
    Descuentos,
    DetalleLiquidacion,
    Liquidacion,
    ListadoMedico,
    LoteAjuste,
    Pago,
    SocioDescuento,
)

router = APIRouter()
TWOPLACES = Decimal("0.01")


# region Helpers
async def _base_bruto_por_medico_en_pago(
    db: AsyncSession, pago_id: int
) -> dict[int, Decimal]:
    """
    Calcula el bruto por médico para un pago.
    DetalleLiquidacion.medico_id guarda NRO_SOCIO; mapeamos a listado_medico.ID (PK real).
    """
    q = await db.execute(
        select(
            ListadoMedico.ID.label("medico_db_id"),
            func.coalesce(func.sum(DetalleLiquidacion.importe), 0),
        )
        .select_from(DetalleLiquidacion)
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .join(ListadoMedico, ListadoMedico.NRO_SOCIO == DetalleLiquidacion.medico_id)
        .where(Liquidacion.pago_id == pago_id)
        .group_by(ListadoMedico.ID)
    )
    out = {}
    for med_id, suma in q:
        out[int(med_id)] = Decimal(suma or 0)
    return out


async def _get_descuento(db: AsyncSession, desc_id: int) -> Descuentos:
    obj = await db.scalar(select(Descuentos).where(Descuentos.id == desc_id))
    if not obj:
        raise ValueError("Descuento inexistente")
    return obj


async def _medicos_para_descuento(db: AsyncSession, desc_id: int) -> List[int]:
    res = await db.execute(
        select(SocioDescuento.medico_id).where(SocioDescuento.descuento_id == desc_id)
    )
    return list({m for (m,) in res.all()})


def _calc_monto(
    p_base: Decimal, precio: Decimal | None, porcentaje: Decimal | None
) -> tuple[Decimal, Decimal | None]:
    if porcentaje and porcentaje > 0:
        m = (p_base * porcentaje / Decimal(100)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
        return (m, porcentaje)
    if precio and precio > 0:
        return (precio.quantize(TWOPLACES, rounding=ROUND_HALF_UP), None)
    return (Decimal(0), None)


async def _disponible_por_medico_en_pago(
    db: AsyncSession, pago_id: int
) -> dict[int, Decimal]:
    """
    Calcula disponible por médico: bruto + créditos (de lotes='L') - débitos (de lotes='L').
    """
    bruto_q = await db.execute(
        select(
            ListadoMedico.ID.label("medico_db_id"),
            func.coalesce(func.sum(DetalleLiquidacion.importe), 0),
        )
        .select_from(DetalleLiquidacion)
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .join(ListadoMedico, ListadoMedico.NRO_SOCIO == DetalleLiquidacion.medico_id)
        .where(Liquidacion.pago_id == pago_id)
        .group_by(ListadoMedico.ID)
    )
    bruto_map = {int(m): Decimal(v or 0) for m, v in bruto_q}

    # Ajustes de lotes en estado='L' del pago
    qaj = await db.execute(
        select(
            Ajuste.medico_id,
            func.coalesce(
                func.sum(case((Ajuste.tipo == "d", Ajuste.monto), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((Ajuste.tipo == "c", Ajuste.monto), else_=0)), 0
            ),
        )
        .select_from(Ajuste)
        .join(LoteAjuste, LoteAjuste.id == Ajuste.lote_id)
        .where(LoteAjuste.pago_id == pago_id, LoteAjuste.estado == "L")
        .group_by(Ajuste.medico_id)
    )
    deb_map, cred_map = {}, {}
    for med, deb, cred in qaj:
        deb_map[int(med)] = Decimal(deb or 0)
        cred_map[int(med)] = Decimal(cred or 0)

    out: dict[int, Decimal] = {}
    keys = set(bruto_map) | set(deb_map) | set(cred_map)
    for k in keys:
        out[k] = (
            bruto_map.get(k, Decimal("0"))
            - deb_map.get(k, Decimal("0"))
            + cred_map.get(k, Decimal("0"))
        )
    return out


# endregion


@router.post("/{pago_id}/colegio/bulk_generar_descuento/{desc_id}")
async def bulk_generar_descuento(
    pago_id: int, desc_id: int, db: AsyncSession = Depends(get_db)
):
    """
    Calcula y registra el monto a descontar para cada médico asignado al descuento.
    Hace UPSERT en Deduccion (snapshot del pago) y acumula en DeduccionSaldo.
    """
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")

    try:
        desc = await _get_descuento(db, desc_id)
    except ValueError:
        raise HTTPException(404, "Descuento no encontrado")

    base_por_med = await _base_bruto_por_medico_en_pago(db, pago_id)

    if getattr(desc, "aplica_a_todos", False):
        med_ids = [mid for mid, bruto in base_por_med.items() if bruto > 0]
    else:
        med_ids = await _medicos_para_descuento(db, desc_id)

    if not med_ids:
        return {"generados": 0, "actualizados": 0, "cargado_total": 0}

    to_snapshot = []
    to_saldo = []

    precio = Decimal(str(getattr(desc, "precio", 0) or 0))
    porcentaje = Decimal(str(getattr(desc, "porcentaje", 0) or 0))
    total_cargado = Decimal(0)

    for mid in med_ids:
        base = base_por_med.get(mid, Decimal(0))
        monto, porc_usado = _calc_monto(base, precio, porcentaje)
        if monto <= 0:
            continue

        total_cargado += monto
        porc_ap = (porc_usado if porc_usado else Decimal("0")).quantize(
            TWOPLACES, rounding=ROUND_HALF_UP
        )

        to_snapshot.append(
            {
                "pago_id": pago_id,
                "medico_id": mid,
                "descuento_id": desc_id,
                "calculado_total": monto,
                "porcentaje_aplicado": porc_ap,
                "monto_aplicado": Decimal("0.00"),
                "pagado": False,
            }
        )
        to_saldo.append(
            {
                "concepto_tipo": "desc",
                "concepto_id": desc_id,
                "medico_id": mid,
                "saldo": monto,
            }
        )

    if not to_snapshot:
        return {"generados": 0, "actualizados": 0, "cargado_total": 0}

    snap_tbl = Deduccion.__table__
    stmt_snap = mysql_insert(snap_tbl).values(to_snapshot)
    up_snap = stmt_snap.on_duplicate_key_update(
        calculado_total=stmt_snap.inserted.calculado_total,
        porcentaje_aplicado=stmt_snap.inserted.porcentaje_aplicado,
    )
    await db.execute(up_snap)

    saldo_tbl = DeduccionSaldo.__table__
    stmt_saldo = mysql_insert(saldo_tbl).values(to_saldo)
    up_saldo = stmt_saldo.on_duplicate_key_update(
        saldo=saldo_tbl.c.saldo + stmt_saldo.inserted.saldo,
    )
    await db.execute(up_saldo)

    await db.commit()

    return {
        "generados": len(to_snapshot),
        "actualizados": 0,
        "cargado_total": float(total_cargado.quantize(TWOPLACES)),
    }


@router.post("/{pago_id}/colegio/aplicar", status_code=status.HTTP_200_OK)
async def aplicar_deducciones_pago(
    pago_id: int,
    desc_id: int | None = Query(None, description="Opcional: aplicar sólo este descuento"),
    solo_generado_mes: bool = Query(True, description="True => sólo lo generado en este pago"),
    db: AsyncSession = Depends(get_db),
):
    """
    Aplica saldos de deducciones al disponible por médico en el pago.
    El disponible = bruto + créditos OS - débitos OS.
    Lo que no entra queda en DeduccionSaldo para períodos futuros.
    """
    async with db.begin():
        pago = await db.get(Pago, pago_id)
        if not pago:
            raise HTTPException(404, "Pago no encontrado")

        disponible_por_med = await _disponible_por_medico_en_pago(db, pago_id)

        base = select(
            DeduccionSaldo.id,
            DeduccionSaldo.medico_id,
            DeduccionSaldo.concepto_id,
            DeduccionSaldo.concepto_tipo,
            DeduccionSaldo.saldo,
        ).where(DeduccionSaldo.concepto_tipo == "desc", DeduccionSaldo.saldo > 0)

        if solo_generado_mes:
            base = base.join(
                Deduccion,
                and_(
                    Deduccion.medico_id == DeduccionSaldo.medico_id,
                    Deduccion.pago_id == pago_id,
                    Deduccion.descuento_id == DeduccionSaldo.concepto_id,
                ),
            )

        if desc_id is not None:
            base = base.where(DeduccionSaldo.concepto_id == desc_id)

        rows = (
            await db.execute(base.order_by(DeduccionSaldo.medico_id, DeduccionSaldo.id))
        ).all()
        if not rows:
            return {
                "pago_id": pago_id,
                "medicos_afectados": 0,
                "aplicado_total": 0.0,
                "nota": "No hay saldos para aplicar bajo los criterios actuales.",
            }

        aplicado_por_med_desc: dict[tuple[int, int, str], Decimal] = defaultdict(
            lambda: Decimal("0.00")
        )
        updates_saldo: list[tuple[int, Decimal]] = []
        aplicados_total = Decimal("0.00")
        medicos_afectados: set[int] = set()

        current_med: int | None = None
        restante = Decimal("0.00")

        for saldo_id, med_id, concepto_id, concepto_tipo, saldo_val in rows:
            med_id = int(med_id)
            concepto_id = int(concepto_id)

            if med_id != current_med:
                current_med = med_id
                restante = Decimal(str(disponible_por_med.get(med_id, 0) or 0))
                if restante <= 0:
                    continue

            if restante <= 0:
                continue

            saldo_dec = Decimal(str(saldo_val or 0))
            if saldo_dec <= 0:
                continue

            tomar = saldo_dec if saldo_dec <= restante else restante
            if tomar <= 0:
                continue

            updates_saldo.append((int(saldo_id), tomar))
            aplicado_por_med_desc[(med_id, concepto_id, concepto_tipo)] += tomar
            aplicados_total += tomar
            medicos_afectados.add(med_id)
            restante -= tomar

        if not updates_saldo:
            return {
                "pago_id": pago_id,
                "medicos_afectados": 0,
                "aplicado_total": 0.0,
                "nota": "No había disponible en el período para aplicar más débitos.",
            }

        # Insertar en DeduccionAplicacion con pago_id, concepto_tipo, concepto_id
        apl_tbl = DeduccionAplicacion.__table__
        apl_values = [
            {
                "pago_id": pago_id,
                "medico_id": med_id,
                "concepto_tipo": conc_tipo,
                "concepto_id": conc_id,
                "aplicado": monto,
            }
            for (med_id, conc_id, conc_tipo), monto in aplicado_por_med_desc.items()
        ]
        stmt_apl = mysql_insert(apl_tbl).values(apl_values)
        up_apl = stmt_apl.on_duplicate_key_update(
            aplicado=apl_tbl.c.aplicado + stmt_apl.inserted.aplicado
        )
        await db.execute(up_apl)

        for saldo_id, aplicado in updates_saldo:
            await db.execute(
                update(DeduccionSaldo)
                .where(DeduccionSaldo.id == saldo_id)
                .values(saldo=DeduccionSaldo.saldo - aplicado)
            )

        # Actualizar monto_aplicado en Deduccion snapshot
        snap_tbl = Deduccion.__table__
        snap_values = [
            {
                "pago_id": pago_id,
                "medico_id": med_id,
                "descuento_id": conc_id,
                "monto_aplicado": monto,
                "calculado_total": Decimal("0.00"),
                "porcentaje_aplicado": Decimal("0.00"),
                "pagado": False,
            }
            for (med_id, conc_id, conc_tipo), monto in aplicado_por_med_desc.items()
            if conc_tipo == "desc"
        ]
        if snap_values:
            stmt_snap = mysql_insert(snap_tbl).values(snap_values)
            up_snap = stmt_snap.on_duplicate_key_update(
                monto_aplicado=snap_tbl.c.monto_aplicado + stmt_snap.inserted.monto_aplicado
            )
            await db.execute(up_snap)

    return {
        "pago_id": pago_id,
        "medicos_afectados": len(medicos_afectados),
        "aplicado_total": float(aplicados_total),
        "nota": "Aplicado respetando el disponible por médico. Remanente queda en saldos.",
    }
