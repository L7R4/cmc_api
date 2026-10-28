"""
Servicios para el módulo de Pagos.
"""
import datetime
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.adelantos.queries import ESTADOS_EN_PAGO, total_adelantos_del_pago
from app.modules.compensaciones.queries import (
    ESTADOS_EN_PAGO as COMP_EN_PAGO,
    leyenda_recibo,
    totales_compensaciones_del_pago,
)
from app.modules.deducciones.helpers import _medicos_en_pago
from app.db.models import (
    Ajuste,
    DeduccionAplicacion,
    Conceptos,
    DetalleLiquidacion,
    Compensacion,
    Deduccion,
    FacturacionCMC,
    Liquidacion,
    LiquidacionAdelanto,
    LiquidacionAdelantoItem,
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
    - total_bruto: suma de brutos de todas las liquidaciones del pago, más los adelantos
    - total_adelantos: la parte de total_bruto que viene de adelantos
    - total_debitos / total_creditos: suma de ajustes de lotes en estado='L' con pago_id dado
    - total_deduccion: suma de deducciones aplicadas al pago
    - total_neto: total_bruto - total_debitos + total_creditos - total_deduccion
    """
    # Bruto de liquidaciones
    bruto_res = await db.execute(
        select(func.coalesce(func.sum(Liquidacion.total_bruto), 0))
        .where(Liquidacion.pago_id == pago_id)
    )
    total_adelantos = _to_dec(await total_adelantos_del_pago(db, pago_id))
    total_bruto = _to_dec(bruto_res.scalar_one()) + total_adelantos

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
        # AP = mismo lote, ya con el pago cerrado — sigue siendo "de este pago".
        .where(LoteAjuste.pago_id == pago_id, LoteAjuste.estado.in_(["L", "AP"]))
    )
    dc_row = dc_res.first()
    total_debitos = _to_dec(dc_row.debitos if dc_row else 0)
    total_creditos = _to_dec(dc_row.creditos if dc_row else 0)

    # Deducciones: pago cerrado (C o P) → suma lo efectivamente aplicado; abierto → suma lo en_pago
    pago = await db.get(Pago, pago_id)
    if pago and pago.estado in ("C", "P"):
        ded_res = await db.execute(
            select(func.coalesce(func.sum(DeduccionAplicacion.aplicado), 0))
            .where(DeduccionAplicacion.pago_id == pago_id)
        )
    else:
        ded_res = await db.execute(
            select(func.coalesce(func.sum(Deduccion.monto_aplicado_preview), 0))
            .where(Deduccion.estado == "en_pago")
        )

    total_deduccion = _to_dec(ded_res.scalar_one())

    # Las compensaciones no son producción (no están en total_bruto) pero sí suman al neto.
    comp_favor, comp_contra = await totales_compensaciones_del_pago(db, pago_id)
    total_compensaciones = _to_dec(comp_favor - comp_contra)

    total_neto = (
        total_bruto - total_debitos + total_creditos + total_compensaciones - total_deduccion
    ).quantize(Decimal("0.01"))

    return {
        "total_bruto": total_bruto,
        "total_adelantos": total_adelantos,
        "total_compensaciones": total_compensaciones,
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
            Liquidacion.factura_id,
            Liquidacion.factura_version,
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
        .order_by(Liquidacion.obra_social_id, Liquidacion.anio_periodo, Liquidacion.mes_periodo, Liquidacion.factura_version)
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
            "factura_id":         r["factura_id"],
            "factura_version":    r["factura_version"],
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
    pago_cerrado = pago.estado in ("C", "P")
    # Mismo criterio que recalcular_totales_pago (la tira de arriba) y que el cierre:
    #  - Cerrado (C o P): lo realmente descontado en ESTE pago, según el ledger
    #    (DeduccionAplicacion), venga la deducción de donde venga.
    #  - Abierto: todas las que están en_pago —las generadas en este pago y también las
    #    enroladas (pendientes que entraron solas)—, con su preview. Filtrar por
    #    generado_en_pago_id dejaba afuera las enroladas y el total no coincidía con la tira.
    if pago_cerrado:
        ded_query = (
            select(
                Deduccion.descuento_id,
                Conceptos.nombre.label("descuento_nombre"),
                func.count(func.distinct(Deduccion.id)).label("cantidad_socios"),
                func.coalesce(func.sum(DeduccionAplicacion.aplicado), 0).label("total_monto"),
            )
            .select_from(DeduccionAplicacion)
            .join(Deduccion, Deduccion.id == DeduccionAplicacion.deduccion_id)
            .outerjoin(Conceptos, Conceptos.id == Deduccion.descuento_id)
            .where(DeduccionAplicacion.pago_id == pago_id)
        )
    else:
        ded_query = (
            select(
                Deduccion.descuento_id,
                Conceptos.nombre.label("descuento_nombre"),
                func.count(Deduccion.id).label("cantidad_socios"),
                func.coalesce(func.sum(Deduccion.monto_aplicado_preview), 0).label("total_monto"),
            )
            .outerjoin(Conceptos, Conceptos.id == Deduccion.descuento_id)
            .where(Deduccion.estado == "en_pago")
        )
    ded_rows = (await db.execute(
        ded_query.group_by(Deduccion.descuento_id, Conceptos.nombre).order_by(Conceptos.nombre)
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

    # ── 4. Adelantos ──────────────────────────────────────────────────────────
    A, I = LiquidacionAdelanto, LiquidacionAdelantoItem
    adel_rows = (await db.execute(
        select(
            A.id.label("adelanto_id"), A.estado, A.obra_social_id,
            ObrasSociales.OBRA_SOCIAL.label("obra_social_nombre"),
            FacturacionCMC.nro_factura, A.mes_periodo, A.anio_periodo,
            func.count(I.id).label("prestaciones"),
            func.count(func.distinct(I.medico_id)).label("medicos"),
            func.coalesce(func.sum(I.honorarios), 0).label("total_honorarios"),
            func.coalesce(func.sum(I.gastos), 0).label("total_gastos"),
            func.coalesce(func.sum(I.importe_total), 0).label("total_bruto"),
        )
        .select_from(A)
        .join(I, I.adelanto_id == A.id)
        .join(ObrasSociales, ObrasSociales.NRO_OBRASOCIAL == A.obra_social_id)
        .outerjoin(FacturacionCMC, FacturacionCMC.id_prestaciones == A.factura_id)
        .where(A.pago_id == pago_id, A.estado.in_(ESTADOS_EN_PAGO))
        .group_by(A.id, A.estado, A.obra_social_id, ObrasSociales.OBRA_SOCIAL,
                  FacturacionCMC.nro_factura, A.mes_periodo, A.anio_periodo)
        .order_by(A.id)
    )).mappings().all()
    adel_items = [
        {
            "adelanto_id":        r["adelanto_id"],
            "estado":             r["estado"],
            "obra_social_id":     r["obra_social_id"],
            "obra_social_nombre": r["obra_social_nombre"],
            "nro_factura":        r["nro_factura"],
            "mes_periodo":        r["mes_periodo"],
            "anio_periodo":       r["anio_periodo"],
            "prestaciones":       r["prestaciones"],
            "medicos":            r["medicos"],
            "total_honorarios":   _to_dec(r["total_honorarios"]),
            "total_gastos":       _to_dec(r["total_gastos"]),
            "total_bruto":        _to_dec(r["total_bruto"]),
        }
        for r in adel_rows
    ]
    adel_tot = sum((a["total_bruto"] for a in adel_items), Decimal("0")).quantize(Decimal("0.01"))

    # ── 5. Compensaciones ─────────────────────────────────────────────────────
    comp_rows = (await db.execute(
        select(Compensacion, ListadoMedico.NOMBRE.label("medico_nombre"))
        .join(ListadoMedico, ListadoMedico.ID == Compensacion.medico_id)
        .where(Compensacion.pago_id == pago_id, Compensacion.estado.in_(COMP_EN_PAGO))
        .order_by(Compensacion.id)
    )).all()
    comp_items = [
        {
            "compensacion_id": r[0].id,
            "estado":          r[0].estado,
            "medico_id":       r[0].medico_id,
            "medico_nombre":   (r.medico_nombre or "").strip(),
            "leyenda":         leyenda_recibo(r[0]),
            "importe":         _to_dec(r[0].importe),
        }
        for r in comp_rows
    ]
    comp_favor = sum((c["importe"] for c in comp_items if c["importe"] > 0), Decimal("0"))
    comp_contra = sum((-c["importe"] for c in comp_items if c["importe"] < 0), Decimal("0"))

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
        "adelantos": {
            "items": adel_items,
            "totales": {"total_bruto": adel_tot},
        },
        "compensaciones": {
            "items": comp_items,
            "totales": {
                "a_favor":   comp_favor.quantize(Decimal("0.01")),
                "en_contra": comp_contra.quantize(Decimal("0.01")),
                "neto":      (comp_favor - comp_contra).quantize(Decimal("0.01")),
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
    guardar: bool = True,
) -> dict:
    """
    Recalcula y persiste PagoMedico + Recibo para un médico en un pago.
    Con `guardar=False` sólo calcula y devuelve (lo usa la consulta de lectura).
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
    # DetalleLiquidacion.medico_id == listado_medico.ID
    liq_rows = (await db.execute(
        select(
            Liquidacion.id.label("liq_id"),
            Liquidacion.obra_social_id,
            Liquidacion.factura_id,
            Liquidacion.nro_factura,
            Liquidacion.factura_version,
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
            DetalleLiquidacion.medico_id == medico_db_id,
        )
        .group_by(
            Liquidacion.id,
            Liquidacion.obra_social_id,
            Liquidacion.factura_id,
            Liquidacion.nro_factura,
            Liquidacion.factura_version,
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
            LoteAjuste.factura_id,
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

    # ── 2b. Adelantos del médico en este pago (una fila por adelanto) ─────────
    A, I = LiquidacionAdelanto, LiquidacionAdelantoItem
    adelanto_rows = (await db.execute(
        select(
            A.id.label("adelanto_id"), A.obra_social_id,
            ObrasSociales.OBRA_SOCIAL.label("obra_social_nombre"),
            FacturacionCMC.nro_factura, FacturacionCMC.version.label("factura_version"),
            A.mes_periodo, A.anio_periodo,
            func.count(I.id).label("prestaciones"),
            func.coalesce(func.sum(I.honorarios), 0).label("honorarios"),
            func.coalesce(func.sum(I.gastos), 0).label("gastos"),
            func.coalesce(func.sum(I.importe_total), 0).label("importe"),
        )
        .select_from(I)
        .join(A, A.id == I.adelanto_id)
        .join(ObrasSociales, ObrasSociales.NRO_OBRASOCIAL == A.obra_social_id)
        .outerjoin(FacturacionCMC, FacturacionCMC.id_prestaciones == A.factura_id)
        .where(
            A.pago_id == pago_id,
            A.estado.in_(ESTADOS_EN_PAGO),
            I.medico_id == medico_db_id,
        )
        .group_by(A.id, A.obra_social_id, ObrasSociales.OBRA_SOCIAL, FacturacionCMC.nro_factura,
                  FacturacionCMC.version, A.mes_periodo, A.anio_periodo)
        .order_by(A.id)
    )).all()

    # ── 3. Construir mapa de liquidaciones ────────────────────────────────────
    liq_map: dict[int, dict] = {}
    factura_to_liq: dict[int, int] = {}
    os_per_to_liq: dict[tuple, int] = {}
    total_liq_honorarios = Decimal("0")
    total_liq_gastos = Decimal("0")
    total_liq_bruto = Decimal("0")

    for r in liq_rows:
        h = _to_dec(r.total_honorarios)
        g = _to_dec(r.total_gastos)
        total_liq_honorarios += h
        total_liq_gastos += g
        total_liq_bruto += _to_dec(r.total_bruto)
        liq_map[r.liq_id] = {
            "obra_social_id":   r.obra_social_id,
            "obra_social":      r.obra_social_nombre,
            "periodo":          f"{r.mes_periodo:02d}/{r.anio_periodo}",
            "nro_factura":      r.nro_factura,
            "factura_version":  r.factura_version,
            "total_honorarios": h,
            "total_gastos":     g,
            "total_bruto":      _to_dec(r.total_bruto),
            "debitos":          {"total": Decimal("0"), "detalle": []},
            "creditos":         {"total": Decimal("0"), "detalle": []},
        }
        if r.factura_id is not None:
            factura_to_liq[r.factura_id] = r.liq_id
        else:
            os_per_to_liq[(r.obra_social_id, r.mes_periodo, r.anio_periodo)] = r.liq_id

    # El adelanto es producción del médico: suma al bruto, a los honorarios y a los gastos.
    adelantos_out: list[dict] = []
    total_adelantos = Decimal("0")
    for r in adelanto_rows:
        h, g, imp = _to_dec(r.honorarios), _to_dec(r.gastos), _to_dec(r.importe)
        total_liq_honorarios += h
        total_liq_gastos += g
        total_liq_bruto += imp
        total_adelantos += imp
        adelantos_out.append({
            "adelanto_id":      r.adelanto_id,
            "obra_social_id":   r.obra_social_id,
            "obra_social":      r.obra_social_nombre,
            "nro_factura":      r.nro_factura,
            "factura_version":  r.factura_version,
            "periodo":          f"{r.mes_periodo:02d}/{r.anio_periodo}",
            "prestaciones":     r.prestaciones,
            "total_honorarios": float(h),
            "total_gastos":     float(g),
            "total":            float(imp),
        })

    # Compensaciones del médico en este pago: no son producción (no tocan bruto, honorarios ni
    # gastos, así que no pagan porcentuales); suman o restan al reconocido.
    comp_rows_med = (await db.execute(
        select(Compensacion)
        .where(
            Compensacion.pago_id == pago_id,
            Compensacion.estado.in_(COMP_EN_PAGO),
            Compensacion.medico_id == medico_db_id,
        )
        .order_by(Compensacion.id)
    )).scalars().all()
    compensaciones_out = [
        {"compensacion_id": c.id, "leyenda": leyenda_recibo(c), "importe": float(_to_dec(c.importe))}
        for c in comp_rows_med
    ]
    total_compensaciones = sum((_to_dec(c.importe) for c in comp_rows_med), Decimal("0"))

    # Distribuir cada ajuste individual en la liquidación de su factura.
    # Un ajuste cuya factura no está en este pago (p. ej. un lote sin_factura o
    # el de una complementaria que se paga en otra corrida) no se descarta:
    # cuenta igual en los totales del médico y se lista aparte en
    # "otros_ajustes", para que el recibo cuadre con lo que ya se le restó del
    # disponible a la hora de calcular deducciones.
    otros_ajustes: list[dict] = []
    otros_os_ids: set[int] = set()
    for r in ajuste_rows:
        if r.factura_id is not None:
            liq_id = factura_to_liq.get(r.factura_id)
        else:
            liq_id = os_per_to_liq.get((r.obra_social_id, r.mes_periodo, r.anio_periodo))
        h = _to_dec(r.honorarios)
        g = _to_dec(r.gastos)
        if liq_id is None:
            otros_os_ids.add(r.obra_social_id)
            otros_ajustes.append({
                "ajuste_id":      r.ajuste_id,
                "lote_id":        r.lote_id,
                "obra_social_id": r.obra_social_id,
                "periodo":        f"{r.mes_periodo:02d}/{r.anio_periodo}",
                "tipo":           "debito" if r.tipo == "d" else "credito",
                "honorarios":     float(h),
                "gastos":         float(g),
                "total":          float(h + g),
                "observacion":    r.observacion,
            })
            continue
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

    # Nombres de OS para los ajustes que cayeron fuera de las liquidaciones del pago
    otros_os_nombres: dict[int, str] = {}
    if otros_os_ids:
        rows_os = (await db.execute(
            select(ObrasSociales.NRO_OBRASOCIAL, ObrasSociales.OBRA_SOCIAL)
            .where(ObrasSociales.NRO_OBRASOCIAL.in_(otros_os_ids))
        )).all()
        otros_os_nombres = {r.NRO_OBRASOCIAL: r.OBRA_SOCIAL for r in rows_os}
    for item in otros_ajustes:
        item["obra_social"] = otros_os_nombres.get(item["obra_social_id"], "")

    total_otros_debitos = sum(
        (Decimal(str(a["total"])) for a in otros_ajustes if a["tipo"] == "debito"), Decimal("0")
    )
    total_otros_creditos = sum(
        (Decimal(str(a["total"])) for a in otros_ajustes if a["tipo"] == "credito"), Decimal("0")
    )

    # Totales de ajustes y serialización del mapa
    total_debitos = total_otros_debitos
    total_creditos = total_otros_creditos
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
    if pago.estado in ("C", "P"):
        # Pago cerrado (C o P): usamos lo efectivamente aplicado via DeduccionAplicacion
        ded_rows = (await db.execute(
            select(
                Conceptos.nro_colegio.label("nro_deduccion"),
                Conceptos.nombre.label("nombre_deduccion"),
                Deduccion.medico_id.label("titular_id"),
                Deduccion.mes_aplicar,
                Deduccion.anio_aplicar,
                func.coalesce(func.sum(DeduccionAplicacion.aplicado), 0).label("total"),
            )
            .select_from(DeduccionAplicacion)
            .join(Deduccion, Deduccion.id == DeduccionAplicacion.deduccion_id)
            .outerjoin(Conceptos, Conceptos.id == Deduccion.descuento_id)
            .where(
                DeduccionAplicacion.pago_id == pago_id,
                # Lo descuenta quien paga (el pagador, si lo hay): es de quien sale la plata.
                func.coalesce(Deduccion.pagador_medico_id, Deduccion.medico_id) == medico_db_id,
            )
            .group_by(
                Conceptos.nro_colegio,
                Conceptos.nombre,
                Deduccion.medico_id,
                Deduccion.mes_aplicar,
                Deduccion.anio_aplicar,
            )
            .order_by(Deduccion.anio_aplicar, Deduccion.mes_aplicar)
        )).all()
    else:
        # Pago abierto: todavía no se cobró nada realmente, mostramos la preview
        ded_rows = (await db.execute(
            select(
                Conceptos.nro_colegio.label("nro_deduccion"),
                Conceptos.nombre.label("nombre_deduccion"),
                Deduccion.medico_id.label("titular_id"),
                Deduccion.mes_aplicar,
                Deduccion.anio_aplicar,
                func.coalesce(func.sum(Deduccion.monto_aplicado_preview), 0).label("total"),
                # Para «Quitar del pago»: sólo los porcentuales automáticos (las cuotas no se quitan).
                func.group_concat(Deduccion.id).label("ids"),
                func.max(case(
                    ((Deduccion.origen == "automatico") & (Deduccion.porcentaje_aplicado > 0), 1), else_=0
                )).label("quitable"),
            )
            .select_from(Deduccion)
            .outerjoin(Conceptos, Conceptos.id == Deduccion.descuento_id)
            .where(
                # Todas las en_pago de este médico, no sólo las generadas en este pago: las
                # cuotas y deudas pendientes que entran solas tienen generado_en_pago_id NULL
                # y quedaban fuera de la vista previa (el neto salía más alto que el real).
                func.coalesce(Deduccion.pagador_medico_id, Deduccion.medico_id) == medico_db_id,
                Deduccion.estado == "en_pago",
            )
            .group_by(
                Conceptos.nro_colegio,
                Conceptos.nombre,
                Deduccion.medico_id,
                Deduccion.mes_aplicar,
                Deduccion.anio_aplicar,
            )
            .order_by(Deduccion.anio_aplicar, Deduccion.mes_aplicar)
        )).all()

    # Deducciones de otro médico que este paga (el pagador): el recibo aclara por quién.
    titulares_ajenos = {r.titular_id for r in ded_rows if r.titular_id != medico_db_id}
    titular_info: dict[int, dict] = {}
    if titulares_ajenos:
        for tid, nro, nombre in (await db.execute(
            select(ListadoMedico.ID, ListadoMedico.NRO_SOCIO, ListadoMedico.NOMBRE)
            .where(ListadoMedico.ID.in_(titulares_ajenos))
        )).all():
            titular_info[tid] = {"nro_socio": nro, "nombre": nombre}

    total_deducciones = Decimal("0")
    ded_detalle = []
    for r in ded_rows:
        t = _to_dec(r.total)
        total_deducciones += t
        mes  = r.mes_aplicar
        anio = r.anio_aplicar
        item = {
            "nro_deduccion":    r.nro_deduccion,
            "nombre_deduccion": r.nombre_deduccion or "Manual",
            "periodo_a_aplicar": f"{mes:02d}/{anio}" if mes and anio else None,
            "total": float(t),
        }
        if r.titular_id != medico_db_id:
            item["por_cuenta_de"] = titular_info.get(r.titular_id, {"nro_socio": None, "nombre": None})
        ids = getattr(r, "ids", None)
        if ids:
            item["ids"] = [int(i) for i in str(ids).split(",") if i]
            item["quitable"] = bool(getattr(r, "quitable", 0))
        ded_detalle.append(item)

    deducciones_out = {
        "total":   float(total_deducciones),
        "detalle": ded_detalle,
    }

    # ── 5. Resumen ────────────────────────────────────────────────────────────
    # bruto = suma de importe_total (mismo campo que usa el header del pago vía
    # Liquidacion.total_bruto/recalcular_totales_de_liquidacion) — no
    # honorarios+gastos, que puede diferir por coseguro u otros ajustes al
    # desglose que no tocan el importe ya facturado por CMC (ver diagnóstico M4).
    total_bruto  = total_liq_bruto
    reconocido   = (total_bruto + total_creditos - total_debitos + total_compensaciones).quantize(Decimal("0.01"))
    neto_a_pagar = max(reconocido - total_deducciones, Decimal("0")).quantize(Decimal("0.01"))

    resumen = {
        "honorarios":  float(total_liq_honorarios.quantize(Decimal("0.01"))),
        "gastos":      float(total_liq_gastos.quantize(Decimal("0.01"))),
        "bruto":       float(total_bruto.quantize(Decimal("0.01"))),
        "debitos":     float(total_debitos.quantize(Decimal("0.01"))),
        "creditos":    float(total_creditos.quantize(Decimal("0.01"))),
        "compensaciones": float(total_compensaciones.quantize(Decimal("0.01"))),
        "reconocido":  float(reconocido),
        "deducciones": float(total_deducciones.quantize(Decimal("0.01"))),
        "neto_a_pagar": float(neto_a_pagar),
    }

    otros_ajustes_out = {
        "total_debitos":  float(total_otros_debitos.quantize(Decimal("0.01"))),
        "total_creditos": float(total_otros_creditos.quantize(Decimal("0.01"))),
        "detalle": otros_ajustes,
    }

    full_doc = {
        "info_medico": info_medico,
        "resumen":     resumen,
        "detalle": {
            "liquidaciones":  liquidaciones_out,
            "adelantos":      {"total": float(total_adelantos.quantize(Decimal("0.01"))), "detalle": adelantos_out},
            "compensaciones": {"total": float(total_compensaciones.quantize(Decimal("0.01"))), "detalle": compensaciones_out},
            "deducciones":    deducciones_out,
            "otros_ajustes":  otros_ajustes_out,
        },
    }

    if not guardar:
        return {medico_db_id: full_doc}

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

    estado_recibo = "liquidado" if pago.estado in ("C", "P") else "en_revision"
    now = datetime.datetime.now()

    existing = (await db.execute(
        select(Recibo).where(Recibo.pago_medico_id == pm.id)
    )).scalars().first()

    if existing and existing.estado == "emitido":
        # Un recibo emitido es el punto de no retorno: no se vuelve a tocar.
        return existing

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


async def quitar_resumenes_de_ausentes(db: AsyncSession, pago_id: int, presentes: set[int]) -> int:
    """
    Borra el resumen (PagoMedico) y el recibo sin emitir de los médicos que ya no participan
    en el pago: el que salió con un lote, un adelanto o una compensación después de «Generar
    recibos». Su resumen viejo daba un recibo o una deuda falsa al cerrar (N16).
    Un recibo emitido no se toca, ni su resumen. Sin commit.
    """
    ausentes = [
        pm for pm in (await db.execute(
            select(PagoMedico).where(PagoMedico.pago_id == pago_id)
        )).scalars().all()
        if pm.medico_id not in presentes
    ]
    if not ausentes:
        return 0
    emitidos = set((await db.execute(
        select(Recibo.medico_id).where(
            Recibo.pago_id == pago_id,
            Recibo.medico_id.in_([pm.medico_id for pm in ausentes]),
            Recibo.estado.in_(["emitido", "pagado"]),
        )
    )).scalars().all())
    ausentes = [pm for pm in ausentes if pm.medico_id not in emitidos]
    if not ausentes:
        return 0
    ids = [pm.medico_id for pm in ausentes]
    await db.execute(
        delete(Recibo).where(Recibo.pago_id == pago_id, Recibo.medico_id.in_(ids))
    )
    await db.execute(
        delete(PagoMedico).where(PagoMedico.pago_id == pago_id, PagoMedico.medico_id.in_(ids))
    )
    await db.flush()
    return len(ids)


async def refrescar_todos_medicos(db: AsyncSession, pago_id: int, pago: Pago) -> dict:
    """
    Llama a refrescar_detalle_medico para cada médico que participa en el pago: con
    prestaciones o con algún ajuste (un crédito de un lote sin factura, por ejemplo).
    Antes borra el resumen de los que ya no participan.
    Devuelve {medico_db_id: {info_medico, resumen, detalle}, ...}.
    """
    presentes = await _medicos_en_pago(db, pago_id)
    await quitar_resumenes_de_ausentes(db, pago_id, presentes)
    medico_ids = sorted(presentes)

    result: dict = {}
    for mid in medico_ids:
        r = await refrescar_detalle_medico(db, pago_id, mid, pago=pago)
        result.update(r)
    return result


async def informe_pago(db: AsyncSession, pago_id: int, tipo: str) -> dict:
    """
    Devuelve médicos que participaron en el pago filtrados por tipo de informe:
    - santander:    cuenta_bancaria == 'santander'
    - otros_bancos: cuenta_bancaria == 'otro_banco'
    - cuit_30:      CUIT comienza con '30'
    - cheques:      cuenta_bancaria == 'cheque'
    """
    from sqlalchemy import and_

    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")

    stmt = (
        select(
            ListadoMedico.ID.label("medico_id"),
            ListadoMedico.NRO_SOCIO.label("nro_socio"),
            ListadoMedico.NOMBRE.label("nombre"),
            ListadoMedico.CUIT.label("cuit"),
            ListadoMedico.cbu.label("cbu"),
            ListadoMedico.cuenta_bancaria.label("cuenta_bancaria"),
            PagoMedico.neto_a_pagar.label("neto_a_pagar"),
        )
        .select_from(PagoMedico)
        .join(ListadoMedico, ListadoMedico.ID == PagoMedico.medico_id)
        .where(PagoMedico.pago_id == pago_id)
    )

    if tipo == "santander":
        stmt = stmt.where(ListadoMedico.cuenta_bancaria == "santander")
    elif tipo == "otros_bancos":
        stmt = stmt.where(ListadoMedico.cuenta_bancaria == "otro_banco")
    elif tipo == "cheques":
        stmt = stmt.where(ListadoMedico.cuenta_bancaria == "cheque")
    elif tipo == "cuit_30":
        stmt = stmt.where(ListadoMedico.CUIT.like("30%"))
    else:
        raise HTTPException(400, f"Tipo de informe inválido: {tipo}")

    stmt = stmt.order_by(ListadoMedico.NOMBRE)
    rows = (await db.execute(stmt)).mappings().all()

    medicos = [
        {
            "medico_id": r["medico_id"],
            "nro_socio": r["nro_socio"],
            "nombre": r["nombre"],
            "cuit": r["cuit"],
            "cbu": r["cbu"],
            "cuenta_bancaria": r["cuenta_bancaria"],
            "neto_a_pagar": _to_dec(r["neto_a_pagar"]),
        }
        for r in rows
    ]

    total_neto = sum((m["neto_a_pagar"] for m in medicos), Decimal("0")).quantize(Decimal("0.01"))

    return {
        "pago_id": pago_id,
        "tipo": tipo,
        "total_neto": total_neto,
        "cantidad": len(medicos),
        "medicos": medicos,
    }


async def generar_todos_recibos(
    db: AsyncSession,
    pago_id: int,
    medico_ids: Optional[list[int]] = None,
    pago: Optional[Pago] = None,
) -> list[Recibo]:
    """
    Genera/actualiza recibos para los médicos indicados (o todos si medico_ids es None).
    Copia el detalle_json desde PagoMedico al Recibo.

    Antes recalcula el resumen de cada médico, así el recibo nunca sale con montos viejos.
    No se genera recibo a un médico que no tiene prestaciones en el pago y no tiene nada
    para cobrar (por ejemplo, sólo débitos): su deuda no es un recibo.
    """
    if pago is None:
        pago = await db.get(Pago, pago_id)
        if not pago:
            raise HTTPException(404, "Pago no encontrado")

    if medico_ids:
        presentes = await _medicos_en_pago(db, pago_id)
        await quitar_resumenes_de_ausentes(db, pago_id, presentes)
        medico_ids = [mid for mid in medico_ids if mid in presentes]
        if not medico_ids:
            raise HTTPException(404, "Los médicos indicados no participan en este pago")
        for mid in medico_ids:
            await refrescar_detalle_medico(db, pago_id, mid, pago=pago)
    else:
        await refrescar_todos_medicos(db, pago_id, pago)

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
        if _to_dec(pm.bruto) == 0 and _to_dec(pm.neto_a_pagar) <= 0:
            # Sin prestaciones ni nada para cobrar: si había un recibo sin emitir, se retira.
            await db.execute(
                delete(Recibo).where(Recibo.pago_medico_id == pm.id, Recibo.estado != "emitido")
            )
            continue
        recibo = await generar_recibo_medico(db, pago_id, pm.medico_id, pago=pago)
        recibos.append(recibo)
    await db.flush()
    return recibos
