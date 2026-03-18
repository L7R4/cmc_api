"""
Servicios para el módulo de Pagos.

Reemplaza la lógica de LiquidacionResumen / generar_liquidacion_medico / emitir_recibos.
"""
import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Ajuste,
    DeduccionAplicacion,
    DetalleLiquidacion,
    Liquidacion,
    ListadoMedico,
    LoteAjuste,
    Pago,
    PagoMedico,
    Recibo,
)


def _to_dec(v) -> Decimal:
    if v is None:
        return Decimal("0")
    try:
        return Decimal(str(v)).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0")


async def recalcular_totales_pago(db: AsyncSession, pago_id: int) -> dict:
    """
    Recalcula los totales globales de un pago:
    - total_bruto: suma de brutos de todas las liquidaciones del pago
    - total_debitos / total_creditos: suma de ajustes de lotes en estado='L' con pago_id dado
    - total_deduccion: suma de deducciones aplicadas al pago
    - total_neto: total_bruto - total_debitos + total_creditos - total_deduccion
    """
    # Bruto de liquidaciones
    bruto_res = await db.execute(
        select(func.coalesce(func.sum(Liquidacion.total_bruto), 0))
        .where(Liquidacion.pago_id == pago_id)
    )
    total_bruto = _to_dec(bruto_res.scalar_one())

    # Débitos y créditos de ajustes en lotes liquidados (estado='L') de este pago
    dc_res = await db.execute(
        select(
            func.coalesce(
                func.sum(case((Ajuste.tipo == "d", Ajuste.monto), else_=0)), 0
            ).label("debitos"),
            func.coalesce(
                func.sum(case((Ajuste.tipo == "c", Ajuste.monto), else_=0)), 0
            ).label("creditos"),
        )
        .select_from(Ajuste)
        .join(LoteAjuste, LoteAjuste.id == Ajuste.lote_id)
        .where(LoteAjuste.pago_id == pago_id, LoteAjuste.estado == "L")
    )
    dc_row = dc_res.first()
    total_debitos = _to_dec(dc_row.debitos if dc_row else 0)
    total_creditos = _to_dec(dc_row.creditos if dc_row else 0)

    # Deducciones aplicadas
    ded_res = await db.execute(
        select(func.coalesce(func.sum(DeduccionAplicacion.aplicado), 0))
        .where(DeduccionAplicacion.pago_id == pago_id)
    )
    total_deduccion = _to_dec(ded_res.scalar_one())

    total_neto = (total_bruto - total_debitos + total_creditos - total_deduccion).quantize(Decimal("0.01"))

    return {
        "total_bruto": total_bruto,
        "total_debitos": total_debitos,
        "total_creditos": total_creditos,
        "total_deduccion": total_deduccion,
        "total_neto": total_neto,
    }


async def generar_pago_medico(db: AsyncSession, pago_id: int) -> List[Dict[str, Any]]:
    """
    Calcula y persiste/actualiza PagoMedico para todos los médicos del pago.
    - bruto = suma DetalleLiquidacion.importe donde liq.pago_id = pago_id y medico_id = X
    - debitos = suma Ajuste.monto WHERE tipo='d' y lote.pago_id=pago_id y lote.estado='L' y ajuste.medico_id=X
    - creditos = suma Ajuste.monto WHERE tipo='c' mismas condiciones
    - reconocido = bruto + creditos - debitos
    - deducciones = suma DeduccionAplicacion.aplicado WHERE pago_id=pago_id y medico_id=X
    - neto_a_pagar = max(0, reconocido - deducciones)
    Upsert en PagoMedico por (pago_id, medico_id).
    """
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")

    # Bruto por médico (medico_id en DetalleLiquidacion es NRO_SOCIO; resolvemos a listado_medico.ID)
    bruto_q = await db.execute(
        select(
            ListadoMedico.ID.label("medico_db_id"),
            func.coalesce(func.sum(DetalleLiquidacion.importe), 0).label("bruto"),
        )
        .select_from(DetalleLiquidacion)
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .join(ListadoMedico, ListadoMedico.NRO_SOCIO == DetalleLiquidacion.medico_id)
        .where(Liquidacion.pago_id == pago_id)
        .group_by(ListadoMedico.ID)
    )
    bruto_map = {int(m): _to_dec(b) for m, b in bruto_q.all()}

    # Débitos y créditos por médico de lotes liquidados
    dc_q = await db.execute(
        select(
            Ajuste.medico_id,
            func.coalesce(
                func.sum(case((Ajuste.tipo == "d", Ajuste.monto), else_=0)), 0
            ).label("debitos"),
            func.coalesce(
                func.sum(case((Ajuste.tipo == "c", Ajuste.monto), else_=0)), 0
            ).label("creditos"),
        )
        .select_from(Ajuste)
        .join(LoteAjuste, LoteAjuste.id == Ajuste.lote_id)
        .where(LoteAjuste.pago_id == pago_id, LoteAjuste.estado == "L")
        .group_by(Ajuste.medico_id)
    )
    deb_map: dict[int, Decimal] = {}
    cred_map: dict[int, Decimal] = {}
    for med_id, deb, cred in dc_q.all():
        deb_map[int(med_id)] = _to_dec(deb)
        cred_map[int(med_id)] = _to_dec(cred)

    # Deducciones aplicadas por médico
    ded_q = await db.execute(
        select(
            DeduccionAplicacion.medico_id,
            func.coalesce(func.sum(DeduccionAplicacion.aplicado), 0).label("total"),
        )
        .where(DeduccionAplicacion.pago_id == pago_id)
        .group_by(DeduccionAplicacion.medico_id)
    )
    ded_map = {int(m): _to_dec(t) for m, t in ded_q.all()}

    todos_medicos = set(bruto_map) | set(deb_map) | set(cred_map) | set(ded_map)

    resultado = []

    for med_id in todos_medicos:
        bruto = bruto_map.get(med_id, Decimal("0"))
        debitos = deb_map.get(med_id, Decimal("0"))
        creditos = cred_map.get(med_id, Decimal("0"))
        deducciones = ded_map.get(med_id, Decimal("0"))

        reconocido = bruto + creditos - debitos
        neto_raw = reconocido - deducciones
        neto_a_pagar = max(neto_raw, Decimal("0"))

        # Upsert PagoMedico
        existing = (await db.execute(
            select(PagoMedico).where(
                PagoMedico.pago_id == pago_id,
                PagoMedico.medico_id == med_id,
            )
        )).scalars().first()

        if existing:
            existing.bruto = bruto
            existing.debitos = debitos
            existing.creditos = creditos
            existing.reconocido = reconocido
            existing.deducciones = deducciones
            existing.neto_a_pagar = neto_a_pagar
            existing.estado = "liquidado"
        else:
            pm = PagoMedico(
                pago_id=pago_id,
                medico_id=med_id,
                bruto=bruto,
                debitos=debitos,
                creditos=creditos,
                reconocido=reconocido,
                deducciones=deducciones,
                neto_a_pagar=neto_a_pagar,
                estado="liquidado",
            )
            db.add(pm)

        resultado.append({
            "medico_id": med_id,
            "bruto": float(bruto),
            "debitos": float(debitos),
            "creditos": float(creditos),
            "reconocido": float(reconocido),
            "deducciones": float(deducciones),
            "neto_a_pagar": float(neto_a_pagar),
        })

    await db.flush()
    return resultado


async def emitir_recibos(db: AsyncSession, pago_id: int) -> List[Dict[str, Any]]:
    """
    Genera Recibo por médico. Upsert por (pago_id, medico_id).
    Precondición: al menos un PagoMedico existe para este pago.
    nro_recibo = f"{pago_id:04d}-{medico_id}"
    """
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")

    pm_rows = (await db.execute(
        select(PagoMedico).where(PagoMedico.pago_id == pago_id)
    )).scalars().all()

    if not pm_rows:
        raise HTTPException(
            409,
            "Ejecutar generar_pago_medico antes de emitir recibos.",
        )

    now = datetime.datetime.now()
    resultado = []

    for pm in pm_rows:
        nro = f"{pago_id:04d}-{pm.medico_id}"

        existing = (await db.execute(
            select(Recibo).where(
                Recibo.pago_id == pago_id,
                Recibo.medico_id == pm.medico_id,
            )
        )).scalars().first()

        if existing:
            # Actualizar siempre (recalcular)
            existing.estado = "emitido"
            existing.emision_timestamp = now
            existing.total_neto = pm.neto_a_pagar
            recibo = existing
        else:
            recibo = Recibo(
                nro_recibo=nro,
                pago_id=pago_id,
                medico_id=pm.medico_id,
                total_neto=pm.neto_a_pagar,
                emision_timestamp=now,
                estado="emitido",
            )
            db.add(recibo)

        resultado.append({
            "medico_id": pm.medico_id,
            "nro_recibo": nro,
            "total_neto": float(pm.neto_a_pagar),
            "estado": "emitido",
        })

    await db.flush()
    return resultado
