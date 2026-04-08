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
    Descuentos,
    DetalleLiquidacion,
    Deduccion,
    Especialidad,
    Liquidacion,
    ListadoMedico,
    LoteAjuste,
    ObrasSociales,
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
                func.sum(case((Ajuste.tipo == "d", Ajuste.honorarios + Ajuste.gastos), else_=0)), 0
            ).label("debitos"),
            func.coalesce(
                func.sum(case((Ajuste.tipo == "c", Ajuste.honorarios + Ajuste.gastos), else_=0)), 0
            ).label("creditos"),
        )
        .select_from(Ajuste)
        .join(LoteAjuste, LoteAjuste.id == Ajuste.lote_id)
        .where(LoteAjuste.pago_id == pago_id, LoteAjuste.estado == "L")
    )
    dc_row = dc_res.first()
    total_debitos = _to_dec(dc_row.debitos if dc_row else 0)
    total_creditos = _to_dec(dc_row.creditos if dc_row else 0)

    ded_res = 0
    # Deducciones aplicadas
    if Pago.estado == "C":
        ded_res = await db.execute(
        select(func.coalesce(func.sum(DeduccionAplicacion.aplicado), 0))
        .where(DeduccionAplicacion.pago_id == pago_id)
    )
    else:
        ded_res = await db.execute(
            select(func.coalesce(func.sum(Deduccion.calculado_total), 0))
            .where(Deduccion.pago_id == pago_id)
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


async def resumen_pago(db: AsyncSession, pago_id: int) -> dict:
    """
    Resumen completo de un pago:
    - facturas: cada liquidación con totales de bruto, débitos, créditos, neto
    - conceptos_descuento: deducciones aplicadas agrupadas por concepto con nombre resuelto
    - totales: bruto, débitos, créditos, reconocido, deducciones, neto final
    """
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")

    # --- 1. Facturas (liquidaciones con nombre OS) ---
    liq_rows = (await db.execute(
        select(
            Liquidacion.id.label("liquidacion_id"),
            Liquidacion.obra_social_id,
            ObrasSociales.OBRA_SOCIAL.label("obra_social_nombre"),
            Liquidacion.nro_factura,
            Liquidacion.mes_periodo,
            Liquidacion.anio_periodo,
            Liquidacion.total_bruto,
            Liquidacion.total_debitos,
            Liquidacion.total_creditos,
            Liquidacion.total_neto,
        )
        .join(ObrasSociales, ObrasSociales.NRO_OBRASOCIAL == Liquidacion.obra_social_id)
        .where(Liquidacion.pago_id == pago_id)
        .order_by(Liquidacion.obra_social_id)
    )).mappings().all()

    facturas = [
        {
            "liquidacion_id": r["liquidacion_id"],
            "obra_social_id": r["obra_social_id"],
            "obra_social_nombre": r["obra_social_nombre"],
            "nro_factura": r["nro_factura"],
            "mes_periodo": r["mes_periodo"],
            "anio_periodo": r["anio_periodo"],
            "total_bruto": _to_dec(r["total_bruto"]),
            "total_debitos": _to_dec(r["total_debitos"]),
            "total_creditos": _to_dec(r["total_creditos"]),
            "total_neto": _to_dec(r["total_neto"]),
        }
        for r in liq_rows
    ]

    # --- 2. Conceptos de descuento agrupados ---
    apl_rows = (await db.execute(
        select(
            DeduccionAplicacion.concepto_tipo,
            DeduccionAplicacion.concepto_id,
            func.coalesce(func.sum(DeduccionAplicacion.aplicado), 0).label("total_aplicado"),
        )
        .where(DeduccionAplicacion.pago_id == pago_id)
        .group_by(DeduccionAplicacion.concepto_tipo, DeduccionAplicacion.concepto_id)
        .order_by(DeduccionAplicacion.concepto_tipo, DeduccionAplicacion.concepto_id)
    )).all()

    # Resolver nombres para desc y esp
    desc_ids = [r.concepto_id for r in apl_rows if r.concepto_tipo == "desc"]
    esp_ids  = [r.concepto_id for r in apl_rows if r.concepto_tipo == "esp"]

    desc_nombres: dict[int, str] = {}
    if desc_ids:
        for row in (await db.execute(
            select(Descuentos.id, Descuentos.nombre).where(Descuentos.id.in_(desc_ids))
        )).all():
            desc_nombres[row.id] = row.nombre

    esp_nombres: dict[int, str] = {}
    if esp_ids:
        for row in (await db.execute(
            select(Especialidad.ID, Especialidad.ESPECIALIDAD).where(Especialidad.ID.in_(esp_ids))
        )).all():
            esp_nombres[row.ID] = row.ESPECIALIDAD

    conceptos_descuento = [
        {
            "concepto_tipo": r.concepto_tipo,
            "concepto_id": r.concepto_id,
            "nombre": (
                desc_nombres.get(r.concepto_id, f"Descuento #{r.concepto_id}")
                if r.concepto_tipo == "desc"
                else esp_nombres.get(r.concepto_id, f"Especialidad #{r.concepto_id}")
            ),
            "total_aplicado": _to_dec(r.total_aplicado),
        }
        for r in apl_rows
    ]

    # --- 3. Totales globales ---
    totales = await recalcular_totales_pago(db, pago_id)
    total_reconocido = (
        totales["total_bruto"] - totales["total_debitos"] + totales["total_creditos"]
    ).quantize(Decimal("0.01"))

    return {
        "pago_id": pago.id,
        "mes": pago.mes,
        "anio": pago.anio,
        "descripcion": pago.descripcion,
        "estado": pago.estado,
        "facturas": facturas,
        "conceptos_descuento": conceptos_descuento,
        "totales": {
            "total_bruto": totales["total_bruto"],
            "total_debitos": totales["total_debitos"],
            "total_creditos": totales["total_creditos"],
            "total_reconocido": total_reconocido,
            "total_deducciones": totales["total_deduccion"],
            "total_neto": totales["total_neto"],
        },
    }


async def generar_pago_medico(db: AsyncSession, pago_id: int) -> List[Dict[str, Any]]:
    """
    Calcula y persiste/actualiza PagoMedico para todos los médicos del pago.
    - bruto = suma DetalleLiquidacion.importe donde liq.pago_id = pago_id y medico_id = X
    - debitos = suma (Ajuste.honorarios+Ajuste.gastos) WHERE tipo='d' y lote.pago_id=pago_id y lote.estado='L' y ajuste.medico_id=X
    - creditos = suma (Ajuste.honorarios+Ajuste.gastos) WHERE tipo='c' mismas condiciones
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
            func.coalesce(func.sum(DetalleLiquidacion.importe_total), 0).label("bruto"),
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
                func.sum(case((Ajuste.tipo == "d", Ajuste.honorarios + Ajuste.gastos), else_=0)), 0
            ).label("debitos"),
            func.coalesce(
                func.sum(case((Ajuste.tipo == "c", Ajuste.honorarios + Ajuste.gastos), else_=0)), 0
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


async def vista_previa_pago(db: AsyncSession, pago_id: int) -> dict:
    """
    Vista previa resumida de un pago con tres secciones:
      - liquidaciones: facturas con totales individuales y grand-total
      - deducciones: items en_pago (pago abierto) o aplicado (pago cerrado) con grand-total
      - lotes: ajustes vinculados con totales de débito/crédito y grand-total
    """
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")

    # ── 1. Liquidaciones ──────────────────────────────────────────────────────
    liq_rows = (await db.execute(
        select(
            Liquidacion.id.label("liquidacion_id"),
            Liquidacion.obra_social_id,
            ObrasSociales.OBRA_SOCIAL.label("obra_social_nombre"),
            Liquidacion.nro_factura,
            Liquidacion.mes_periodo,
            Liquidacion.anio_periodo,
            Liquidacion.total_bruto,
            Liquidacion.total_debitos,
            Liquidacion.total_creditos,
            Liquidacion.total_neto,
        )
        .join(ObrasSociales, ObrasSociales.NRO_OBRASOCIAL == Liquidacion.obra_social_id)
        .where(Liquidacion.pago_id == pago_id)
        .order_by(Liquidacion.obra_social_id)
    )).mappings().all()

    liq_items = []
    liq_tot_bruto = liq_tot_deb = liq_tot_cred = liq_tot_neto = Decimal("0")
    for r in liq_rows:
        bruto      = _to_dec(r["total_bruto"])
        deb        = _to_dec(r["total_debitos"])
        cred       = _to_dec(r["total_creditos"])
        neto       = _to_dec(r["total_neto"])
        reconocido = (bruto - deb + cred).quantize(Decimal("0.01"))
        liq_items.append({
            "liquidacion_id":    r["liquidacion_id"],
            "obra_social_id":    r["obra_social_id"],
            "obra_social_nombre": r["obra_social_nombre"],
            "nro_factura":       r["nro_factura"],
            "mes_periodo":       r["mes_periodo"],
            "anio_periodo":      r["anio_periodo"],
            "total_bruto":       bruto,
            "total_debitos":     deb,
            "total_creditos":    cred,
            "total_reconocido":  reconocido,
            "total_neto":        neto,
        })
        liq_tot_bruto += bruto
        liq_tot_deb   += deb
        liq_tot_cred  += cred
        liq_tot_neto  += neto

    liq_tot_reconocido = (liq_tot_bruto - liq_tot_deb + liq_tot_cred).quantize(Decimal("0.01"))

    # ── 2. Deducciones agrupadas por concepto ────────────────────────────────
    # Estado a consultar según estado del pago
    estado_ded = "aplicado" if pago.estado == "C" else "en_pago"

    ded_rows = (await db.execute(
        select(
            Deduccion.descuento_id,
            Descuentos.nombre.label("descuento_nombre"),
            func.count(Deduccion.id).label("cantidad_socios"),
            func.coalesce(func.sum(Deduccion.calculado_total), 0).label("total_monto"),
        )
        .outerjoin(Descuentos, Descuentos.id == Deduccion.descuento_id)
        .where(Deduccion.pago_id == pago_id, Deduccion.estado == estado_ded)
        .group_by(Deduccion.descuento_id, Descuentos.nombre)
        .order_by(Descuentos.nombre)
    )).mappings().all()

    ded_items = []
    ded_tot_monto = Decimal("0")
    for r in ded_rows:
        monto = _to_dec(r["total_monto"])
        ded_items.append({
            "descuento_id":     r["descuento_id"],
            "descuento_nombre": r["descuento_nombre"] or "",
            "cantidad_socios":  r["cantidad_socios"],
            "total_monto":      monto,
        })
        ded_tot_monto += monto

    # ── 3. Lotes de ajuste ────────────────────────────────────────────────────
    lote_rows = (await db.execute(
        select(
            LoteAjuste.id.label("lote_id"),
            LoteAjuste.tipo,
            LoteAjuste.obra_social_id,
            ObrasSociales.OBRA_SOCIAL.label("obra_social_nombre"),
            LoteAjuste.mes_periodo,
            LoteAjuste.anio_periodo,
            LoteAjuste.estado,
            LoteAjuste.total_debitos,
            LoteAjuste.total_creditos,
        )
        .join(ObrasSociales, ObrasSociales.NRO_OBRASOCIAL == LoteAjuste.obra_social_id)
        .where(LoteAjuste.pago_id == pago_id)
        .order_by(LoteAjuste.tipo, LoteAjuste.obra_social_id)
    )).mappings().all()

    lote_items = []
    lote_tot_deb = lote_tot_cred = Decimal("0")
    for r in lote_rows:
        deb  = _to_dec(r["total_debitos"])
        cred = _to_dec(r["total_creditos"])
        lote_items.append({
            "lote_id":           r["lote_id"],
            "tipo":              r["tipo"],
            "obra_social_id":    r["obra_social_id"],
            "obra_social_nombre": r["obra_social_nombre"],
            "mes_periodo":       r["mes_periodo"],
            "anio_periodo":      r["anio_periodo"],
            "estado":            r["estado"],
            "total_debitos":     deb,
            "total_creditos":    cred,
        })
        lote_tot_deb  += deb
        lote_tot_cred += cred

    return {
        "pago_id":     pago.id,
        "mes":         pago.mes,
        "anio":        pago.anio,
        "descripcion": pago.descripcion,
        "estado":      pago.estado,
        "liquidaciones": {
            "items": liq_items,
            "totales": {
                "total_bruto":      liq_tot_bruto.quantize(Decimal("0.01")),
                "total_debitos":    liq_tot_deb.quantize(Decimal("0.01")),
                "total_creditos":   liq_tot_cred.quantize(Decimal("0.01")),
                "total_reconocido": liq_tot_reconocido,
                "total_neto":       liq_tot_neto.quantize(Decimal("0.01")),
            },
        },
        "deducciones": {
            "items": ded_items,
            "totales": {
                "total_monto": ded_tot_monto.quantize(Decimal("0.01")),
            },
        },
        "lotes": {
            "items": lote_items,
            "totales": {
                "total_debitos":  lote_tot_deb.quantize(Decimal("0.01")),
                "total_creditos": lote_tot_cred.quantize(Decimal("0.01")),
            },
        },
    }


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
