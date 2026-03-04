from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, case, func, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import (
    Debito_Credito,
    Deduccion,
    DeduccionAplicacion,
    DeduccionSaldo,
    Descuentos,
    DetalleLiquidacion,
    Liquidacion,
    LiquidacionResumen,
    ListadoMedico,
    SocioDescuento,
)

router = APIRouter()
TWOPLACES = Decimal("0.01")


# region Helpers
async def _base_bruto_por_medico_en_resumen(
    db: AsyncSession, resumen_id: int
) -> dict[int, Decimal]:
    # DetalleLiquidacion.medico_id guarda NRO_SOCIO; mapeamos a listado_medico.ID (PK real)
    # para que los keys del dict sean comparables con SocioDescuento.medico_id (FK → ID).
    q = await db.execute(
        select(
            ListadoMedico.ID.label("medico_db_id"),
            func.coalesce(func.sum(DetalleLiquidacion.importe), 0),
        )
        .select_from(DetalleLiquidacion)
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .join(ListadoMedico, ListadoMedico.NRO_SOCIO == DetalleLiquidacion.medico_id)
        .where(Liquidacion.resumen_id == resumen_id)
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


async def _disponible_por_medico_en_resumen(
    db: AsyncSession, resumen_id: int
) -> dict[int, Decimal]:
    # DetalleLiquidacion.medico_id guarda NRO_SOCIO; resolvemos a listado_medico.ID
    # para que los keys sean comparables con DeduccionSaldo.medico_id (FK → ID).
    bruto = await db.execute(
        select(
            ListadoMedico.ID.label("medico_db_id"),
            func.coalesce(func.sum(DetalleLiquidacion.importe), 0),
        )
        .select_from(DetalleLiquidacion)
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .join(ListadoMedico, ListadoMedico.NRO_SOCIO == DetalleLiquidacion.medico_id)
        .where(Liquidacion.resumen_id == resumen_id)
        .group_by(ListadoMedico.ID)
    )
    bruto_map = {int(m): Decimal(v or 0) for m, v in bruto}

    # DCs via detalle_liquidacion_id (N DCs por detalle), resolviendo NRO_SOCIO → ID
    qdc = await db.execute(
        select(
            ListadoMedico.ID.label("medico_db_id"),
            func.coalesce(
                func.sum(case((Debito_Credito.tipo == "d", Debito_Credito.monto), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((Debito_Credito.tipo == "c", Debito_Credito.monto), else_=0)), 0
            ),
        )
        .select_from(DetalleLiquidacion)
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .join(ListadoMedico, ListadoMedico.NRO_SOCIO == DetalleLiquidacion.medico_id)
        .outerjoin(
            Debito_Credito,
            Debito_Credito.detalle_liquidacion_id == DetalleLiquidacion.id,
        )
        .where(Liquidacion.resumen_id == resumen_id)
        .group_by(ListadoMedico.ID)
    )
    deb_map, cred_map = {}, {}
    for med, deb, cred in qdc:
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


@router.post("/{resumen_id}/colegio/bulk_generar_descuento/{desc_id}")
async def bulk_generar_descuento(
    resumen_id: int, desc_id: int, db: AsyncSession = Depends(get_db)
):
    """
    Calcula y registra el monto a descontar para cada médico asignado al descuento.
    Hace UPSERT en Deduccion (snapshot del resumen) y acumula en DeduccionSaldo.
    """
    # Verificar que el resumen existe
    resumen = await db.get(LiquidacionResumen, resumen_id)
    if not resumen:
        raise HTTPException(404, "Resumen no encontrado")

    try:
        desc = await _get_descuento(db, desc_id)
    except ValueError:
        raise HTTPException(404, "Descuento no encontrado")

    med_ids = await _medicos_para_descuento(db, desc_id)
    if not med_ids:
        return {"generados": 0, "actualizados": 0, "cargado_total": 0}

    base_por_med = await _base_bruto_por_medico_en_resumen(db, resumen_id)

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

        # AGENT DO IT: incluir resumen_id + anio/mes (de resumen) en Deduccion
        to_snapshot.append(
            {
                "resumen_id": resumen_id,
                "medico_id": mid,
                "descuento_id": desc_id,
                "anio": resumen.anio,
                "mes": resumen.mes,
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


@router.post("/{resumen_id}/colegio/aplicar", status_code=status.HTTP_200_OK)
async def aplicar_deducciones_resumen(
    resumen_id: int,
    desc_id: int | None = Query(None, description="Opcional: aplicar sólo este descuento"),
    solo_generado_mes: bool = Query(True, description="True => sólo lo generado en este resumen"),
    db: AsyncSession = Depends(get_db),
):
    """
    Aplica saldos de deducciones al disponible por médico en el resumen.
    El disponible = bruto + créditos OS - débitos OS.
    Lo que no entra queda en DeduccionSaldo para períodos futuros.
    """
    async with db.begin():
        res = await db.get(LiquidacionResumen, resumen_id)
        if not res:
            raise HTTPException(404, "Resumen no encontrado")

        disponible_por_med = await _disponible_por_medico_en_resumen(db, resumen_id)

        base = select(
            DeduccionSaldo.id,
            DeduccionSaldo.medico_id,
            DeduccionSaldo.concepto_id,
            DeduccionSaldo.concepto_tipo,
            DeduccionSaldo.saldo,
        ).where(DeduccionSaldo.concepto_tipo == "desc", DeduccionSaldo.saldo > 0)

        # AGENT DO IT: join con Deduccion via resumen_id (antes era via anio/mes)
        if solo_generado_mes:
            base = base.join(
                Deduccion,
                and_(
                    Deduccion.medico_id == DeduccionSaldo.medico_id,
                    Deduccion.resumen_id == resumen_id,
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
                "resumen_id": resumen_id,
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
                "resumen_id": resumen_id,
                "medicos_afectados": 0,
                "aplicado_total": 0.0,
                "nota": "No había disponible en el período para aplicar más débitos.",
            }

        # AGENT DO IT: insertar en DeduccionAplicacion con resumen_id, concepto_tipo, concepto_id
        apl_tbl = DeduccionAplicacion.__table__
        apl_values = [
            {
                "resumen_id": resumen_id,
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
                "resumen_id": resumen_id,
                "medico_id": med_id,
                "anio": res.anio,
                "mes": res.mes,
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

        # Recalcular total_deduccion del resumen
        qsum = await db.execute(
            select(func.coalesce(func.sum(DeduccionAplicacion.aplicado), 0)).where(
                DeduccionAplicacion.resumen_id == resumen_id
            )
        )
        res.total_deduccion = Decimal(qsum.scalar_one() or 0).quantize(Decimal("0.01"))

    return {
        "resumen_id": resumen_id,
        "medicos_afectados": len(medicos_afectados),
        "aplicado_total": float(aplicados_total),
        "nota": "Aplicado respetando el disponible por médico. Remanente queda en saldos.",
    }
