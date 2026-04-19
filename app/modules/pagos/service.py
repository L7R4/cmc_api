"""
Servicios para el módulo de Pagos.
"""
import datetime
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Ajuste,
    DeduccionAplicacion,
    Descuentos,
    DetalleLiquidacion,
    Deduccion,
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

    # Deducciones: pago cerrado → suma lo efectivamente aplicado; abierto → suma lo en_pago
    pago = await db.get(Pago, pago_id)
    if pago and pago.estado == "C":
        ded_res = await db.execute(
            select(func.coalesce(func.sum(DeduccionAplicacion.aplicado), 0))
            .where(DeduccionAplicacion.pago_id == pago_id)
        )
    else:
        ded_res = await db.execute(
            select(func.coalesce(func.sum(Deduccion.monto_aplicado), 0))
            .where(Deduccion.estado == "en_pago")
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
            Liquidacion.total_honorarios,
            Liquidacion.total_gastos,
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
    liq_tot_honorarios = liq_tot_gastos = Decimal("0")
    liq_tot_bruto = liq_tot_deb = liq_tot_cred = liq_tot_neto = Decimal("0")
    for r in liq_rows:
        honorarios = _to_dec(r["total_honorarios"])
        gastos     = _to_dec(r["total_gastos"])
        bruto      = _to_dec(r["total_bruto"])
        deb        = _to_dec(r["total_debitos"])
        cred       = _to_dec(r["total_creditos"])
        neto       = _to_dec(r["total_neto"])
        reconocido = (bruto - deb + cred).quantize(Decimal("0.01"))
        liq_items.append({
            "liquidacion_id":     r["liquidacion_id"],
            "obra_social_id":     r["obra_social_id"],
            "obra_social_nombre": r["obra_social_nombre"],
            "nro_factura":        r["nro_factura"],
            "mes_periodo":        r["mes_periodo"],
            "anio_periodo":       r["anio_periodo"],
            "total_honorarios":   honorarios,
            "total_gastos":       gastos,
            "total_bruto":        bruto,
            "total_debitos":      deb,
            "total_creditos":     cred,
            "total_reconocido":   reconocido,
            "total_neto":         neto,
        })
        liq_tot_honorarios += honorarios
        liq_tot_gastos     += gastos
        liq_tot_bruto      += bruto
        liq_tot_deb        += deb
        liq_tot_cred       += cred
        liq_tot_neto       += neto

    liq_tot_reconocido = (liq_tot_bruto - liq_tot_deb + liq_tot_cred).quantize(Decimal("0.01"))

    # ── 2. Deducciones agrupadas por concepto ────────────────────────────────
    estado_ded = "aplicado" if pago.estado == "C" else "en_pago"

    ded_rows = (await db.execute(
        select(
            Deduccion.descuento_id,
            Descuentos.nombre.label("descuento_nombre"),
            func.count(Deduccion.id).label("cantidad_socios"),
            func.coalesce(func.sum(Deduccion.monto_aplicado), 0).label("total_monto"),
        )
        .outerjoin(Descuentos, Descuentos.id == Deduccion.descuento_id)
        .where(Deduccion.generado_en_pago_id == pago_id, Deduccion.estado == estado_ded)
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
            "lote_id":            r["lote_id"],
            "tipo":               r["tipo"],
            "obra_social_id":     r["obra_social_id"],
            "obra_social_nombre": r["obra_social_nombre"],
            "mes_periodo":        r["mes_periodo"],
            "anio_periodo":       r["anio_periodo"],
            "estado":             r["estado"],
            "total_debitos":      deb,
            "total_creditos":     cred,
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
                "total_honorarios": liq_tot_honorarios.quantize(Decimal("0.01")),
                "total_gastos":     liq_tot_gastos.quantize(Decimal("0.01")),
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


# ================================================
# Refrescar detalle de un médico en un pago
# ================================================
async def refrescar_detalle_medico(
    db: AsyncSession,
    pago_id: int,
    medico_db_id: int,
    pago: Optional[Pago] = None,
) -> dict:
    """
    Recalcula y persiste PagoMedico + Recibo para un médico en un pago.
    Devuelve {medico_db_id: {info_medico, resumen, detalle}}.

    Nota: medico_db_id es ListadoMedico.ID (PK interna), no NRO_SOCIO.
    """
    if pago is None:
        pago = await db.get(Pago, pago_id)
        if not pago:
            raise HTTPException(404, "Pago no encontrado")

    medico = await db.get(ListadoMedico, medico_db_id)
    if not medico:
        raise HTTPException(404, f"Médico ID {medico_db_id} no encontrado")

    info_medico = {
        "id": medico.ID,
        "nro_socio": medico.NRO_SOCIO,
        "matricula": medico.MATRICULA_PROV,
        "nombre": medico.NOMBRE,
    }

    # ── 1. Liquidaciones donde participó el médico ────────────────────────────
    # DetalleLiquidacion.medico_id == NRO_SOCIO (legacy)
    liq_rows = (await db.execute(
        select(
            Liquidacion.id.label("liq_id"),
            Liquidacion.obra_social_id,
            ObrasSociales.OBRA_SOCIAL.label("obra_social_nombre"),
            Liquidacion.mes_periodo,
            Liquidacion.anio_periodo,
            func.coalesce(func.sum(DetalleLiquidacion.honorarios), 0).label("total_honorarios"),
            func.coalesce(func.sum(DetalleLiquidacion.gastos), 0).label("total_gastos"),
            func.coalesce(func.sum(DetalleLiquidacion.importe_total), 0).label("total_bruto"),
        )
        .select_from(DetalleLiquidacion)
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .join(ObrasSociales, ObrasSociales.NRO_OBRASOCIAL == Liquidacion.obra_social_id)
        .where(
            Liquidacion.pago_id == pago_id,
            DetalleLiquidacion.medico_id == medico.NRO_SOCIO,
        )
        .group_by(
            Liquidacion.id,
            Liquidacion.obra_social_id,
            ObrasSociales.OBRA_SOCIAL,
            Liquidacion.mes_periodo,
            Liquidacion.anio_periodo,
        )
    )).all()

    # ── 2. Ajustes individuales del médico en este pago ──────────────────────
    # Un row por cada Ajuste (no agrupado), con contexto del lote
    ajuste_rows = (await db.execute(
        select(
            Ajuste.id.label("ajuste_id"),
            Ajuste.tipo,
            Ajuste.honorarios,
            Ajuste.gastos,
            Ajuste.observacion,
            LoteAjuste.id.label("lote_id"),
            LoteAjuste.obra_social_id,
            LoteAjuste.mes_periodo,
            LoteAjuste.anio_periodo,
        )
        .select_from(Ajuste)
        .join(LoteAjuste, LoteAjuste.id == Ajuste.lote_id)
        .where(
            LoteAjuste.pago_id == pago_id,
            Ajuste.medico_id == medico_db_id,
        )
        .order_by(LoteAjuste.id, Ajuste.id)
    )).all()

    # ── 3. Construir mapa de liquidaciones ────────────────────────────────────
    liq_map: dict[int, dict] = {}
    os_per_to_liq: dict[tuple, int] = {}
    total_liq_honorarios = Decimal("0")
    total_liq_gastos = Decimal("0")

    for r in liq_rows:
        h = _to_dec(r.total_honorarios)
        g = _to_dec(r.total_gastos)
        total_liq_honorarios += h
        total_liq_gastos += g
        liq_map[r.liq_id] = {
            "obra_social_id":   r.obra_social_id,
            "obra_social":      r.obra_social_nombre,
            "periodo":          f"{r.mes_periodo:02d}/{r.anio_periodo}",
            "total_honorarios": h,
            "total_gastos":     g,
            "total_bruto":      _to_dec(r.total_bruto),
            "debitos":          {"total": Decimal("0"), "detalle": []},
            "creditos":         {"total": Decimal("0"), "detalle": []},
        }
        os_per_to_liq[(r.obra_social_id, r.mes_periodo, r.anio_periodo)] = r.liq_id

    # Distribuir cada ajuste individual en su liquidación correspondiente
    for r in ajuste_rows:
        liq_id = os_per_to_liq.get((r.obra_social_id, r.mes_periodo, r.anio_periodo))
        if liq_id is None:
            continue
        h = _to_dec(r.honorarios)
        g = _to_dec(r.gastos)
        item = {
            "ajuste_id":  r.ajuste_id,
            "lote_id":    r.lote_id,
            "honorarios": float(h),
            "gastos":     float(g),
            "total":      float(h + g),
            "observacion": r.observacion,
        }
        bucket = "debitos" if r.tipo == "d" else "creditos"
        liq_map[liq_id][bucket]["detalle"].append(item)
        liq_map[liq_id][bucket]["total"] += h + g

    # Totales de ajustes y serialización del mapa
    total_debitos = Decimal("0")
    total_creditos = Decimal("0")
    liquidaciones_out: dict[str, dict] = {}

    for liq_id, data in liq_map.items():
        deb_total  = data["debitos"]["total"]
        cred_total = data["creditos"]["total"]
        total_debitos  += deb_total
        total_creditos += cred_total
        liquidaciones_out[str(liq_id)] = {
            "obra_social":      data["obra_social"],
            "periodo":          data["periodo"],
            "total_honorarios": float(data["total_honorarios"]),
            "total_gastos":     float(data["total_gastos"]),
            "total_bruto":      float(data["total_bruto"]),
            "debitos":  {"total": float(deb_total),  "detalle": data["debitos"]["detalle"]},
            "creditos": {"total": float(cred_total), "detalle": data["creditos"]["detalle"]},
        }

    # ── 4. Deducciones agrupadas por (nro_colegio + periodo_a_aplicar) ────────
    if pago.estado == "C":
        # Pago cerrado: usamos lo efectivamente aplicado via DeduccionAplicacion
        ded_rows = (await db.execute(
            select(
                Descuentos.nro_colegio.label("nro_deduccion"),
                Descuentos.nombre.label("nombre_deduccion"),
                Deduccion.mes_aplicar,
                Deduccion.anio_aplicar,
                func.coalesce(func.sum(DeduccionAplicacion.aplicado), 0).label("total"),
            )
            .select_from(DeduccionAplicacion)
            .join(Deduccion, Deduccion.id == DeduccionAplicacion.deduccion_id)
            .outerjoin(Descuentos, Descuentos.id == Deduccion.descuento_id)
            .where(
                DeduccionAplicacion.pago_id == pago_id,
                Deduccion.medico_id == medico_db_id,
            )
            .group_by(
                Descuentos.nro_colegio,
                Descuentos.nombre,
                Deduccion.mes_aplicar,
                Deduccion.anio_aplicar,
            )
            .order_by(Deduccion.anio_aplicar, Deduccion.mes_aplicar)
        )).all()
    else:
        # Pago abierto: usamos el monto_aplicado pendiente en el pago
        ded_rows = (await db.execute(
            select(
                Descuentos.nro_colegio.label("nro_deduccion"),
                Descuentos.nombre.label("nombre_deduccion"),
                Deduccion.mes_aplicar,
                Deduccion.anio_aplicar,
                func.coalesce(func.sum(Deduccion.monto_aplicado), 0).label("total"),
            )
            .select_from(Deduccion)
            .outerjoin(Descuentos, Descuentos.id == Deduccion.descuento_id)
            .where(
                Deduccion.generado_en_pago_id == pago_id,
                Deduccion.medico_id == medico_db_id,
                Deduccion.estado == "en_pago",
            )
            .group_by(
                Descuentos.nro_colegio,
                Descuentos.nombre,
                Deduccion.mes_aplicar,
                Deduccion.anio_aplicar,
            )
            .order_by(Deduccion.anio_aplicar, Deduccion.mes_aplicar)
        )).all()

    total_deducciones = Decimal("0")
    ded_detalle = []
    for r in ded_rows:
        t = _to_dec(r.total)
        total_deducciones += t
        mes  = r.mes_aplicar
        anio = r.anio_aplicar
        ded_detalle.append({
            "nro_deduccion":    r.nro_deduccion,
            "nombre_deduccion": r.nombre_deduccion or "Manual",
            "periodo_a_aplicar": f"{mes:02d}/{anio}" if mes and anio else None,
            "total": float(t),
        })

    deducciones_out = {
        "total":   float(total_deducciones),
        "detalle": ded_detalle,
    }

    # ── 5. Resumen ────────────────────────────────────────────────────────────
    total_bruto  = total_liq_honorarios + total_liq_gastos
    reconocido   = (total_bruto + total_creditos - total_debitos).quantize(Decimal("0.01"))
    neto_a_pagar = max(reconocido - total_deducciones, Decimal("0")).quantize(Decimal("0.01"))

    resumen = {
        "honorarios":  float(total_liq_honorarios.quantize(Decimal("0.01"))),
        "gastos":      float(total_liq_gastos.quantize(Decimal("0.01"))),
        "bruto":       float(total_bruto.quantize(Decimal("0.01"))),
        "debitos":     float(total_debitos.quantize(Decimal("0.01"))),
        "creditos":    float(total_creditos.quantize(Decimal("0.01"))),
        "reconocido":  float(reconocido),
        "deducciones": float(total_deducciones.quantize(Decimal("0.01"))),
        "neto_a_pagar": float(neto_a_pagar),
    }

    full_doc = {
        "info_medico": info_medico,
        "resumen":     resumen,
        "detalle": {
            "liquidaciones": liquidaciones_out,
            "deducciones":   deducciones_out,
        },
    }

    # ── 6. Upsert PagoMedico ──────────────────────────────────────────────────
    existing_pm = (await db.execute(
        select(PagoMedico).where(
            PagoMedico.pago_id == pago_id,
            PagoMedico.medico_id == medico_db_id,
        )
    )).scalars().first()

    pm_vals = dict(
        honorarios=total_liq_honorarios,
        gastos=total_liq_gastos,
        bruto=total_bruto,
        debitos=total_debitos,
        creditos=total_creditos,
        reconocido=reconocido,
        deducciones=total_deducciones,
        neto_a_pagar=neto_a_pagar,
        estado="liquidado",
        detalle_json=full_doc,
    )
    if existing_pm:
        for k, v in pm_vals.items():
            setattr(existing_pm, k, v)
    else:
        db.add(PagoMedico(pago_id=pago_id, medico_id=medico_db_id, **pm_vals))

    await db.flush()
    return {medico_db_id: full_doc}


async def generar_recibo_medico(
    db: AsyncSession,
    pago_id: int,
    medico_db_id: int,
    pago: Optional[Pago] = None,
) -> Recibo:
    """
    Crea o actualiza el Recibo de un médico en un pago a partir de su PagoMedico.
    El estado inicial es 'en_revision' (pago abierto) o 'liquidado' (pago cerrado).
    """
    if pago is None:
        pago = await db.get(Pago, pago_id)
        if not pago:
            raise HTTPException(404, "Pago no encontrado")

    pm = (await db.execute(
        select(PagoMedico).where(
            PagoMedico.pago_id == pago_id,
            PagoMedico.medico_id == medico_db_id,
        )
    )).scalars().first()
    if not pm:
        raise HTTPException(404, f"PagoMedico no encontrado para médico {medico_db_id} en pago {pago_id}")

    estado_recibo = "liquidado" if pago.estado == "C" else "en_revision"
    now = datetime.datetime.now()

    existing = (await db.execute(
        select(Recibo).where(Recibo.pago_medico_id == pm.id)
    )).scalars().first()

    if existing:
        existing.total_neto        = pm.neto_a_pagar
        existing.detalle_json      = pm.detalle_json
        existing.emision_timestamp = now
        existing.estado            = estado_recibo
        recibo = existing
    else:
        recibo = Recibo(
            nro_recibo=f"{pago_id:04d}-{medico_db_id}",
            pago_id=pago_id,
            medico_id=medico_db_id,
            pago_medico_id=pm.id,
            total_neto=pm.neto_a_pagar,
            detalle_json=pm.detalle_json,
            emision_timestamp=now,
            estado=estado_recibo,
        )
        db.add(recibo)

    await db.flush()
    return recibo


async def refrescar_todos_medicos(db: AsyncSession, pago_id: int, pago: Pago) -> dict:
    """
    Llama a refrescar_detalle_medico para cada médico con detalles en el pago.
    Devuelve {medico_db_id: {info_medico, resumen, detalle}, ...}.
    """
    medico_ids_q = await db.execute(
        select(ListadoMedico.ID.label("medico_db_id"))
        .select_from(DetalleLiquidacion)
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .join(ListadoMedico, ListadoMedico.NRO_SOCIO == DetalleLiquidacion.medico_id)
        .where(Liquidacion.pago_id == pago_id)
        .distinct()
    )
    medico_ids = [r.medico_db_id for r in medico_ids_q.all()]

    result: dict = {}
    for mid in medico_ids:
        r = await refrescar_detalle_medico(db, pago_id, mid, pago=pago)
        result.update(r)
    return result


async def generar_todos_recibos(
    db: AsyncSession,
    pago_id: int,
    medico_ids: Optional[list[int]] = None,
    pago: Optional[Pago] = None,
) -> list[Recibo]:
    """
    Genera/actualiza recibos para los médicos indicados (o todos si medico_ids es None).
    Copia el detalle_json desde PagoMedico al Recibo.
    """
    if pago is None:
        pago = await db.get(Pago, pago_id)
        if not pago:
            raise HTTPException(404, "Pago no encontrado")

    if medico_ids:
        stmt = select(PagoMedico).where(
            PagoMedico.pago_id == pago_id,
            PagoMedico.medico_id.in_(medico_ids),
        )
    else:
        stmt = select(PagoMedico).where(PagoMedico.pago_id == pago_id)

    pm_rows = (await db.execute(stmt)).scalars().all()
    if not pm_rows:
        raise HTTPException(404, "No se encontraron PagoMedico para los médicos indicados")

    recibos = []
    for pm in pm_rows:
        recibo = await generar_recibo_medico(db, pago_id, pm.medico_id, pago=pago)
        recibos.append(recibo)
    return recibos
