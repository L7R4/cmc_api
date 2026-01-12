from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, Body, status
from fastapi.params import Query
from pydantic import BaseModel
from typing import Optional, Literal, List, Dict
from decimal import ROUND_HALF_UP, Decimal
from sqlalchemy import and_, or_, select, text, func, case, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import (
    Debito_Credito, LiquidacionResumen, Descuentos, DeduccionColegio,
    DeduccionSaldo, DeduccionAplicacion,
    DetalleLiquidacion, Liquidacion,SocioDescuento
)

from sqlalchemy.dialects.mysql import insert as mysql_insert


router = APIRouter()
TWOPLACES = Decimal("0.01")
class OverrideValores(BaseModel):
    monto: Optional[Decimal] = None
    porcentaje: Optional[Decimal] = None

def _tipo_id_for_desc(desc_id: int) -> tuple[str,int]:
    return ("desc", int(desc_id))

async def _base_bruto_por_medico_en_resumen(db: AsyncSession, resumen_id: int) -> dict[int, Decimal]:
    q = await db.execute(
        select(DetalleLiquidacion.medico_id, func.coalesce(func.sum(DetalleLiquidacion.importe), 0))
        .select_from(DetalleLiquidacion)
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .where(Liquidacion.resumen_id == resumen_id)
        .group_by(DetalleLiquidacion.medico_id)
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
    """
    Devuelve los medico_id habilitados para este descuento (fecha_baja NULL o futura).
    """
    res = await db.execute(
        select(SocioDescuento.medico_id)
        .where(
            SocioDescuento.descuento_id == desc_id,
            # or_(SocioDescuento.fecha_baja.is_(None), SocioDescuento.fecha_baja > func.current_date()),
        )
    )
    # set() para evitar duplicados
    return list({m for (m,) in res.all()})

def _calc_monto(p_base: Decimal, precio: Decimal | None, porcentaje: Decimal | None) -> tuple[Decimal, Decimal | None]:
    """
    Si porcentaje > 0 => monto = base * %.
    Sino, si precio > 0 => monto = precio.
    Sino => 0.
    Devuelve (monto, porcentaje_usado|None)
    """
    if porcentaje and porcentaje > 0:
        m = (p_base * porcentaje / Decimal(100)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
        return (m, porcentaje)
    if precio and precio > 0:
        return (precio.quantize(TWOPLACES, rounding=ROUND_HALF_UP), None)
    return (Decimal(0), None)

@router.post("/{resumen_id}/colegio/bulk_generar_descuento/{desc_id}")
async def bulk_generar_descuento(resumen_id: int, desc_id: int, db: AsyncSession = Depends(get_db)):
    # 1) Traer descuento y médicos asociados
    try:
        desc = await _get_descuento(db, desc_id)
    except ValueError:
        raise HTTPException(404, "Descuento no encontrado")

    med_ids = await _medicos_para_descuento(db, desc_id)
    if not med_ids:
        return {"generados": 0, "actualizados": 0, "cargado_total": 0}

    # 2) Base bruto por médico en el período
    base_por_med = await _base_bruto_por_medico_en_resumen(db, resumen_id)

    # 3) Preparar snapshots del mes (DeduccionColegio) y acumulación (DeduccionSaldo)
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

        # porcentaje_aplicado: nunca NULL (usa 0 si no hay %)
        porc_ap = (porc_usado if porc_usado else Decimal("0")).quantize(TWOPLACES, rounding=ROUND_HALF_UP)

        # IMPORTANTE:
        # - calculado_total: cuánto corresponde este mes por el concepto
        # - monto_aplicado: arranca en 0; se incrementa en /aplicar
        to_snapshot.append({
            "resumen_id": resumen_id,
            "medico_id": mid,
            "descuento_id": desc_id,
            "calculado_total": monto,
            "porcentaje_aplicado": porc_ap,
            "monto_aplicado": Decimal("0.00"),
        })

        to_saldo.append({
            "concepto_tipo": "desc",
            "concepto_id": desc_id,
            "medico_id": mid,
            "saldo": monto,
        })

    if not to_snapshot:
        return {"generados": 0, "actualizados": 0, "cargado_total": 0}

    # 4) UPSERT snapshot del mes
    snap_tbl = DeduccionColegio.__table__
    stmt_snap = mysql_insert(snap_tbl).values(to_snapshot)
    up_snap = stmt_snap.on_duplicate_key_update(
        # si ya existe (resumen_id, medico_id, descuento_id),
        # actualizamos el snapshot del mes pero NO tocamos lo ya aplicado
        calculado_total=stmt_snap.inserted.calculado_total,
        porcentaje_aplicado=stmt_snap.inserted.porcentaje_aplicado,
    )
    await db.execute(up_snap)

    # 5) UPSERT saldo acumulado: sumamos
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

async def _disponible_por_medico_en_resumen(db: AsyncSession, resumen_id: int) -> dict[int, Decimal]:
    # bruto por médico
    bruto = await db.execute(
        select(DetalleLiquidacion.medico_id, func.coalesce(func.sum(DetalleLiquidacion.importe), 0))
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .where(Liquidacion.resumen_id == resumen_id)
        .group_by(DetalleLiquidacion.medico_id)
    )
    bruto_map = {int(m): Decimal(v or 0) for m, v in bruto}

    # débitos y créditos por médico (por DC ligados a sus detalles del resumen)
    qdc = await db.execute(
        select(
            DetalleLiquidacion.medico_id,
            func.coalesce(func.sum(case((Debito_Credito.tipo=="d", Debito_Credito.monto), else_=0)), 0),
            func.coalesce(func.sum(case((Debito_Credito.tipo=="c", Debito_Credito.monto), else_=0)), 0),
        )
        .select_from(DetalleLiquidacion)
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .join(Debito_Credito, DetalleLiquidacion.debito_credito_id == Debito_Credito.id, isouter=True)
        .where(Liquidacion.resumen_id == resumen_id)
        .group_by(DetalleLiquidacion.medico_id)
    )
    deb_map, cred_map = {}, {}
    for med, deb, cred in qdc:
        deb_map[int(med)] = Decimal(deb or 0)
        cred_map[int(med)] = Decimal(cred or 0)

    # disponible = bruto - déb + créd
    out: dict[int, Decimal] = {}
    keys = set(bruto_map) | set(deb_map) | set(cred_map)
    for k in keys:
        out[k] = (bruto_map.get(k, Decimal("0")) - deb_map.get(k, Decimal("0")) + cred_map.get(k, Decimal("0")))
    return out

@router.post("/{resumen_id}/colegio/aplicar", status_code=status.HTTP_200_OK)
async def aplicar_deducciones_resumen(
    resumen_id: int,
    desc_id: int | None = Query(None, description="Opcional: aplicar sólo este descuento"),
    solo_generado_mes: bool = Query(True, description="True => sólo lo generado en este resumen"),
    db: AsyncSession = Depends(get_db),
):
    async with db.begin():
        res = await db.get(LiquidacionResumen, resumen_id)
        if not res:
            raise HTTPException(404, "Resumen no encontrado")

        disponible_por_med = await _disponible_por_medico_en_resumen(db, resumen_id)

        base = select(
            DeduccionSaldo.id,
            DeduccionSaldo.medico_id,
            DeduccionSaldo.concepto_id,   # = descuentos.id
            DeduccionSaldo.saldo
        ).where(
            DeduccionSaldo.concepto_tipo == "desc",
            DeduccionSaldo.saldo > 0
        )

        if solo_generado_mes:
            base = base.join(
                DeduccionColegio,
                and_(
                    DeduccionColegio.medico_id == DeduccionSaldo.medico_id,
                    DeduccionColegio.resumen_id == resumen_id,
                    DeduccionColegio.descuento_id == DeduccionSaldo.concepto_id,
                )
            )

        if desc_id is not None:
            base = base.where(DeduccionSaldo.concepto_id == desc_id)

        rows = (await db.execute(base.order_by(DeduccionSaldo.medico_id, DeduccionSaldo.id))).all()
        if not rows:
            return {
                "resumen_id": resumen_id,
                "medicos_afectados": 0,
                "aplicado_total": 0.0,
                "nota": "No hay saldos para aplicar bajo los criterios actuales."
            }

        aplicado_por_med_desc: dict[tuple[int, int], Decimal] = defaultdict(lambda: Decimal("0.00"))
        updates_saldo: list[tuple[int, Decimal]] = []
        aplicados_total = Decimal("0.00")
        medicos_afectados: set[int] = set()

        current_med: int | None = None
        restante = Decimal("0.00")

        for saldo_id, med_id, concepto_id, saldo_val in rows:
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
            aplicado_por_med_desc[(med_id, concepto_id)] += tomar
            aplicados_total += tomar
            medicos_afectados.add(med_id)
            restante -= tomar

        if not updates_saldo:
            return {
                "resumen_id": resumen_id,
                "medicos_afectados": 0,
                "aplicado_total": 0.0,
                "nota": "No había disponible en el período para aplicar más débitos."
            }

        # 1) Upsert en deduccion_aplicacion (suma aplicado)
        apl_tbl = DeduccionAplicacion.__table__
        apl_values = [
            {
                "resumen_id": resumen_id,
                "medico_id": med_id,
                "concepto_tipo": "desc",
                "concepto_id": d_id,
                "aplicado": monto,
            }
            for (med_id, d_id), monto in aplicado_por_med_desc.items()
        ]
        stmt_apl = mysql_insert(apl_tbl).values(apl_values)
        up_apl = stmt_apl.on_duplicate_key_update(
            aplicado=apl_tbl.c.aplicado + stmt_apl.inserted.aplicado
        )
        await db.execute(up_apl)

        # 2) Descontar saldo (sin updated_at)
        for saldo_id, aplicado in updates_saldo:
            await db.execute(
                update(DeduccionSaldo)
                .where(DeduccionSaldo.id == saldo_id)
                .values(saldo=DeduccionSaldo.saldo - aplicado)
            )

        # 3) Reflejar en snapshot del mes cuánto se aplicó (monto_aplicado += tomado)
        snap_tbl = DeduccionColegio.__table__
        snap_values = [
            {
                "resumen_id": resumen_id,
                "medico_id": med_id,
                "descuento_id": d_id,
                "monto_aplicado": monto,
            }
            for (med_id, d_id), monto in aplicado_por_med_desc.items()
        ]
        stmt_snap = mysql_insert(snap_tbl).values(snap_values)
        up_snap = stmt_snap.on_duplicate_key_update(
            monto_aplicado=snap_tbl.c.monto_aplicado + stmt_snap.inserted.monto_aplicado
        )
        await db.execute(up_snap)

        # 4) Recalcular total_deduccion del resumen
        qsum = await db.execute(
            select(func.coalesce(func.sum(DeduccionAplicacion.aplicado), 0))
            .where(DeduccionAplicacion.resumen_id == resumen_id)
        )
        res.total_deduccion = Decimal(qsum.scalar_one() or 0).quantize(Decimal("0.01"))

    return {
        "resumen_id": resumen_id,
        "medicos_afectados": len(medicos_afectados),
        "aplicado_total": float(aplicados_total),
        "nota": "Aplicado respetando el disponible por médico. Remanente queda en saldos."
    }
