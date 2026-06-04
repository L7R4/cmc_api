# app/modules/liquidacion/service.py
"""
Servicios para el módulo de liquidaciones.
"""
import datetime
import json
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import (
    Ajuste,
    DeduccionAplicacion,
    Deduccion,
    Descuentos,
    DetalleLiquidacion,
    DetalleFacturacionCMC,
    Especialidad,
    ListadoMedico,
    Liquidacion,
    LoteAjuste,
    ObrasSociales,
    Pago,
    PagoMedico,
    Recibo,
)


# ==============================
# Helpers
# ==============================

def _dec(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def to_dec(x) -> Decimal:
    try:
        return Decimal(str(x or "0")).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0")


# ================================================
# Poblar detalles de liquidación desde detalle_facturacion (CMC)
# ================================================
async def build_detalles_from_cmc(db: AsyncSession, liquidacion_id: int) -> None:
    liq = (await db.execute(
        select(Liquidacion).where(Liquidacion.id == liquidacion_id)
    )).scalars().first()
    if not liq:
        return

    anio, mes, os_id = int(liq.anio_periodo), int(liq.mes_periodo), int(liq.obra_social_id)
    periodo = f"{anio}{mes:02d}"
    cod_obr = str(os_id)

    cmc_rows = (await db.execute(
        select(DetalleFacturacionCMC).where(
            DetalleFacturacionCMC.cod_obr == cod_obr,
            DetalleFacturacionCMC.periodo == periodo,
            DetalleFacturacionCMC.estado == "L",
        )
    )).scalars().all()

    if not cmc_rows:
        return

    # Cargar de una vez todos los médicos involucrados para evitar N+1
    cod_meds = {r.cod_med for r in cmc_rows}
    medico_map: dict[str, int] = {}
    lm_rows = (await db.execute(
        select(ListadoMedico.NRO_SOCIO, ListadoMedico.ID).where(
            ListadoMedico.NRO_SOCIO.in_([int(c) for c in cod_meds if c is not None])
        )
    )).all()
    for nro_socio, lm_id in lm_rows:
        medico_map[str(nro_socio)] = lm_id

    # Set de (cmc_detalle_id,) para detectar duplicados
    existing_cmc = set((await db.execute(
        select(DetalleLiquidacion.cmc_detalle_id)
        .where(
            DetalleLiquidacion.liquidacion_id == liquidacion_id,
            DetalleLiquidacion.cmc_detalle_id.isnot(None),
        )
    )).scalars().all())

    observados: list[dict] = []

    for df in cmc_rows:
        if df.id_detalle_prestaciones in existing_cmc:
            continue

        medico_id = medico_map.get(df.cod_med)
        if medico_id is None:
            observados.append({
                "cmc_detalle_id": df.id_detalle_prestaciones,
                "cod_med": df.cod_med,
                "razon": "medico_no_encontrado",
            })
            continue

        funcion = (df.tpo_funcion or "H").upper()
        if funcion == "H":
            honorarios = to_dec(df.honorarios)
            gastos = Decimal("0")
        elif funcion == "HG":
            honorarios = to_dec(df.honorarios)
            gastos = to_dec(df.gastos)
        elif funcion == "G":
            honorarios = Decimal("0")
            gastos = to_dec(df.gastos)
        elif funcion == "A":
            honorarios = to_dec(df.ayudante)
            gastos = Decimal("0")
        else:
            honorarios = to_dec(df.honorarios)
            gastos = Decimal("0")

        importe_total = to_dec(df.importe_total)
        if importe_total <= 0:
            observados.append({
                "cmc_detalle_id": df.id_detalle_prestaciones,
                "cod_med": df.cod_med,
                "razon": "importe_cero_o_negativo",
            })
            continue

        detalle_item = DetalleLiquidacion(
            liquidacion_id=liq.id,
            medico_id=medico_id,
            obra_social_id=os_id,
            prestacion_id=None,
            fuente="cmc",
            cmc_detalle_id=df.id_detalle_prestaciones,
            pagado=Decimal("0"),
            honorarios=honorarios,
            gastos=gastos,
            importe_total=importe_total,
            fecha_practica=df.fecha_practica,
            codigo_prestacion_cmc=df.cod_nom,
            nro_orden_cmc=df.nro_orden,
            paciente_nombre_cmc=df.nom_ape_p,
            paciente_nro_afiliado_cmc=df.dni_p,
            sesion_cmc=df.sesion or 1,
            cantidad_cmc=df.cantidad or 1,
            porcentaje_cmc=df.porc or 100,
        )
        db.add(detalle_item)
        existing_cmc.add(df.id_detalle_prestaciones)

    await db.flush()

    if observados:
        print(f"[build_detalles_from_cmc] Observaciones en liq {liquidacion_id}: {json.dumps(observados)}")


# ================================================
# Recalcular totales de una liquidación (OS)
# ================================================
async def recalcular_totales_de_liquidacion(db: AsyncSession, liquidacion_id: int) -> None:
    liq = (await db.execute(
        select(Liquidacion).where(Liquidacion.id == liquidacion_id)
    )).scalars().first()
    if not liq:
        return

    # Bruto: suma de importes de detalles (y desglose honorarios/gastos)
    bruto_res = await db.execute(
        select(
            func.coalesce(func.sum(DetalleLiquidacion.importe_total), 0).label("bruto"),
            func.coalesce(func.sum(DetalleLiquidacion.honorarios), 0).label("honorarios"),
            func.coalesce(func.sum(DetalleLiquidacion.gastos), 0).label("gastos"),
        )
        .where(DetalleLiquidacion.liquidacion_id == liquidacion_id)
    )
    bruto_row = bruto_res.first()
    total_bruto = to_dec(bruto_row.bruto)
    total_honorarios = to_dec(bruto_row.honorarios)
    total_gastos = to_dec(bruto_row.gastos)

    # DCs de ajustes en lotes en estado='L' del pago, para esta OS+período
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
        .where(
            LoteAjuste.pago_id == liq.pago_id,
            LoteAjuste.obra_social_id == liq.obra_social_id,
            LoteAjuste.mes_periodo == liq.mes_periodo,
            LoteAjuste.anio_periodo == liq.anio_periodo,
            LoteAjuste.estado == "L",
        )
    )
    dc_row = dc_res.first()
    sum_debitos = to_dec(dc_row.debitos if dc_row else 0)
    sum_creditos = to_dec(dc_row.creditos if dc_row else 0)

    liq.total_honorarios = total_honorarios
    liq.total_gastos = total_gastos
    liq.total_bruto = total_bruto
    liq.total_debitos = sum_debitos
    liq.total_creditos = sum_creditos
    liq.total_neto = total_bruto - sum_debitos + sum_creditos

    await db.flush()


# ================================================
# Vista enriquecida de detalles de una liquidación
# ================================================
async def vista_detalles_liquidacion(
    db: AsyncSession,
    liquidacion_id: int,
    medico_id: Optional[int] = None,
    search: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    DL = DetalleLiquidacion
    LM = aliased(ListadoMedico)

    filters = [DL.liquidacion_id == liquidacion_id]

    if medico_id is not None:
        filters.append(DL.medico_id == medico_id)

    if search:
        s = search.strip()
        if s:
            if s.isdigit():
                n = int(s)
                filters.append(
                    or_(
                        LM.NRO_SOCIO == n,
                        DL.medico_id == n,
                        DL.codigo_prestacion_cmc == s,
                    )
                )
            else:
                like = f"%{s}%"
                filters.append(
                    or_(
                        LM.NOMBRE.like(like),
                        DL.codigo_prestacion_cmc.like(like),
                    )
                )

    stmt_base = (
        select(
            DL.id.label("det_id"),
            DL.medico_id.label("socio"),
            LM.NOMBRE.label("nombreSocio"),
            LM.NRO_SOCIO.label("matri"),
            DL.nro_orden_cmc.label("nroOrden_cmc"),
            DL.fecha_practica.label("fecha_cmc"),
            DL.codigo_prestacion_cmc.label("codigo_cmc"),
            DL.paciente_nro_afiliado_cmc.label("nroAfiliado_cmc"),
            DL.paciente_nombre_cmc.label("afiliado_cmc"),
            DL.sesion_cmc.label("sesion_cmc"),
            DL.cantidad_cmc.label("cantidad_cmc"),
            DL.porcentaje_cmc.label("porcentaje_cmc"),
            func.coalesce(DL.honorarios, 0).label("honorarios"),
            func.coalesce(DL.gastos, 0).label("gastos"),
            func.coalesce(DL.importe_total, 0).label("importe_total"),
            func.coalesce(DL.pagado, 0).label("pagado"),
            DL.obra_social_id.label("obra_social_id"),
        )
        .select_from(DL)
        .outerjoin(LM, LM.NRO_SOCIO == DL.medico_id)
        .where(and_(*filters))
        .order_by(DL.id)
    )

    base_rows = (await db.execute(stmt_base)).mappings().all()
    if not base_rows:
        return [], 0

    # Obtener la liquidación para saber pago_id, os, período
    liq = (await db.execute(
        select(Liquidacion).where(Liquidacion.id == liquidacion_id)
    )).scalars().first()

    # Ajustes de lotes en estado 'L' o 'C' del pago para ese OS+período
    ajuste_map: dict[int, list[dict[str, Any]]] = {}
    if liq and liq.pago_id:
        stmt_aj = (
            select(
                Ajuste.id.label("ajuste_id"),
                Ajuste.medico_id.label("medico_id"),
                Ajuste.tipo.label("tipo"),
                Ajuste.honorarios.label("honorarios"),
                Ajuste.gastos.label("gastos"),
                (Ajuste.honorarios + Ajuste.gastos).label("total"),
                Ajuste.observacion.label("obs"),
            )
            .select_from(Ajuste)
            .join(LoteAjuste, LoteAjuste.id == Ajuste.lote_id)
            .where(
                LoteAjuste.pago_id == liq.pago_id,
                LoteAjuste.obra_social_id == liq.obra_social_id,
                LoteAjuste.mes_periodo == liq.mes_periodo,
                LoteAjuste.anio_periodo == liq.anio_periodo,
                LoteAjuste.estado.in_(["L", "C"]),
            )
            .order_by(Ajuste.id)
        )
        aj_rows = (await db.execute(stmt_aj)).mappings().all()

        # Agrupar por medico_id para luego matchear con filas de detalles
        med_ajuste_map: dict[int, list[dict]] = {}
        for a in aj_rows:
            med_id = int(a["medico_id"])
            tipo = (a["tipo"] or "").lower()
            tipo_ui = "D" if tipo == "d" else "C" if tipo == "c" else None
            if not tipo_ui:
                continue
            med_ajuste_map.setdefault(med_id, []).append({
                "ajuste_id": int(a["ajuste_id"]),
                "tipo": tipo_ui,
                "honorarios": float(Decimal(str(a["honorarios"] or "0"))),
                "gastos": float(Decimal(str(a["gastos"] or "0"))),
                "total": float(Decimal(str(a["total"] or "0"))),
                "obs": a["obs"] or None,
            })

        # Asignar por det_id agrupando por medico_id del detalle
        for r in base_rows:
            det_id = int(r["det_id"])
            med_id = int(r["socio"])
            ajuste_map[det_id] = med_ajuste_map.get(med_id, [])

    out: List[Dict[str, Any]] = []
    for r in base_rows:
        importe_total = Decimal(str(r["importe_total"] or "0"))
        pagado = Decimal(str(r["pagado"] or "0"))

        fecha = str(r["fecha_cmc"]) if r["fecha_cmc"] is not None else ""
        codigo = r["codigo_cmc"] or ""
        nroAfiliado = r.get("nroAfiliado_cmc") or None
        afiliado = r.get("afiliado_cmc") or None
        nroOrden = r.get("nroOrden_cmc")
        cantidad = int(r.get("sesion_cmc") or 1)
        cant_trat = int(r.get("cantidad_cmc") or 1)
        porcentaje = float(r.get("porcentaje_cmc") or 100)

        xCant = f"{cantidad}-{cant_trat}"
        det_id = int(r["det_id"])
        aj_list = ajuste_map.get(det_id, [])

        sum_c = sum(Decimal(str(aj["total"] or 0)) for aj in aj_list if aj["tipo"] == "C")
        sum_d = sum(Decimal(str(aj["total"] or 0)) for aj in aj_list if aj["tipo"] == "D")

        total = importe_total + sum_c - sum_d

        out.append({
            "det_id": det_id,
            "socio": r["socio"],
            "nombreSocio": (r["nombreSocio"] or "").strip(),
            "matri": r["matri"],
            "nroOrden": nroOrden,
            "fecha": fecha,
            "codigo": codigo,
            "nroAfiliado": nroAfiliado,
            "afiliado": afiliado,
            "xCant": xCant,
            "porcentaje": porcentaje,
            "honorarios": float(r["honorarios"] or 0),
            "gastos": float(r["gastos"] or 0),
            "coseguro": 0.0,
            "importe_total": float(importe_total),
            "pagado": float(pagado),
            "debitos_creditos_list": aj_list,
            "total": float(total),
        })

    return out, len(out)


# ================================================
# Detalle completo de un recibo/preview para un médico en un pago
# ================================================
async def detalle_recibo_medico(
    db: AsyncSession,
    pago_id: int,
    medico_id: int,  # listado_medico.ID
    recibo: Optional["Recibo"] = None,
) -> Dict[str, Any]:
    """
    Desglose completo para un médico en un pago:
    - Por cada liquidación del pago donde el médico tiene detalles: bruto, ajustes, reconocido
    - Deducciones aplicadas
    - Totales y neto a pagar
    """
    lm = await db.get(ListadoMedico, medico_id)
    if not lm:
        raise HTTPException(404, "Médico no encontrado")
    nro_socio = int(lm.NRO_SOCIO)

    pago_medico = (await db.execute(
        select(PagoMedico).where(
            PagoMedico.pago_id == pago_id,
            PagoMedico.medico_id == medico_id,
        )
    )).scalars().first()

    liquidaciones = (await db.execute(
        select(Liquidacion).where(Liquidacion.pago_id == pago_id)
    )).scalars().all()

    os_ids = list({liq.obra_social_id for liq in liquidaciones})
    os_rows = (await db.execute(
        select(ObrasSociales.NRO_OBRASOCIAL, ObrasSociales.OBRA_SOCIAL)
        .where(ObrasSociales.NRO_OBRASOCIAL.in_(os_ids))
    )).all()
    os_nombre_map = {r.NRO_OBRASOCIAL: r.OBRA_SOCIAL for r in os_rows}

    liq_items = []
    for liq in liquidaciones:
        detalles = (await db.execute(
            select(DetalleLiquidacion).where(
                DetalleLiquidacion.liquidacion_id == liq.id,
                DetalleLiquidacion.medico_id == nro_socio,
            )
        )).scalars().all()

        if not detalles:
            continue

        bruto = sum(Decimal(str(d.importe_total or 0)) for d in detalles)

        # Ajustes del lote en estado='L' del pago para esa OS+período y médico
        aj_rows = (await db.execute(
            select(
                Ajuste.id.label("aj_id"),
                Ajuste.tipo,
                Ajuste.honorarios,
                Ajuste.gastos,
                (Ajuste.honorarios + Ajuste.gastos).label("total"),
                Ajuste.observacion,
                Ajuste.id_atencion,
                DetalleFacturacionCMC.cod_nom.label("codigo"),
                DetalleFacturacionCMC.fecha_practica.label("fecha"),
            )
            .select_from(Ajuste)
            .join(LoteAjuste, LoteAjuste.id == Ajuste.lote_id)
            .outerjoin(DetalleFacturacionCMC,
                       DetalleFacturacionCMC.id_detalle_prestaciones == Ajuste.id_atencion)
            .where(
                LoteAjuste.pago_id == pago_id,
                LoteAjuste.obra_social_id == liq.obra_social_id,
                LoteAjuste.mes_periodo == liq.mes_periodo,
                LoteAjuste.anio_periodo == liq.anio_periodo,
                LoteAjuste.estado == "L",
                Ajuste.medico_id == medico_id,
            )
            .order_by(Ajuste.tipo, Ajuste.id)
        )).mappings().all()

        debitos: list[dict] = []
        creditos: list[dict] = []
        total_d = Decimal("0")
        total_c = Decimal("0")

        for aj in aj_rows:
            total = to_dec(aj["total"])
            entry = {
                "ajuste_id": int(aj["aj_id"]),
                "id_atencion": aj["id_atencion"],
                "codigo_prestacion": aj["codigo"],
                "fecha": str(aj["fecha"]) if aj["fecha"] else None,
                "honorarios": float(to_dec(aj["honorarios"])),
                "gastos": float(to_dec(aj["gastos"])),
                "total": float(total),
                "motivo": aj["observacion"],
            }
            if aj["tipo"] == "d":
                debitos.append(entry)
                total_d += total
            else:
                creditos.append(entry)
                total_c += total

        liq_items.append({
            "liquidacion_id": liq.id,
            "obra_social_id": liq.obra_social_id,
            "obra_social_nombre": os_nombre_map.get(liq.obra_social_id, ""),
            "mes_periodo": liq.mes_periodo,
            "anio_periodo": liq.anio_periodo,
            "nro_factura": liq.nro_factura,
            "bruto": float(bruto),
            "debitos": debitos,
            "total_debitos": float(total_d),
            "creditos": creditos,
            "total_creditos": float(total_c),
            "reconocido": float(bruto + total_c - total_d),
        })

    # Deducciones aplicadas
    apl_rows = (await db.execute(
        select(
            DeduccionAplicacion.concepto_tipo,
            DeduccionAplicacion.concepto_id,
            DeduccionAplicacion.aplicado,
        ).where(
            DeduccionAplicacion.pago_id == pago_id,
            DeduccionAplicacion.medico_id == medico_id,
        )
    )).mappings().all()

    desc_ids = [r["concepto_id"] for r in apl_rows if r["concepto_tipo"] == "desc"]
    esp_ids  = [r["concepto_id"] for r in apl_rows if r["concepto_tipo"] == "esp"]

    desc_nombres: dict[int, str] = {}
    if desc_ids:
        for r in (await db.execute(
            select(Descuentos.id, Descuentos.nombre).where(Descuentos.id.in_(desc_ids))
        )).all():
            desc_nombres[r.id] = r.nombre

    esp_nombres: dict[int, str] = {}
    if esp_ids:
        for r in (await db.execute(
            select(Especialidad.ID, Especialidad.ESPECIALIDAD).where(Especialidad.ID.in_(esp_ids))
        )).all():
            esp_nombres[r.ID] = r.ESPECIALIDAD

    deducciones: list[dict] = []
    total_deducciones = Decimal("0")
    for apl in apl_rows:
        aplicado = to_dec(apl["aplicado"])
        nombre = (
            desc_nombres.get(apl["concepto_id"], f"Descuento #{apl['concepto_id']}")
            if apl["concepto_tipo"] == "desc"
            else esp_nombres.get(apl["concepto_id"], f"Especialidad #{apl['concepto_id']}")
        )
        deducciones.append({
            "concepto_tipo": apl["concepto_tipo"],
            "concepto_id": apl["concepto_id"],
            "nombre": nombre,
            "aplicado": float(aplicado),
        })
        total_deducciones += aplicado

    # Totales
    if pago_medico:
        total_bruto      = float(pago_medico.bruto)
        total_debitos    = float(pago_medico.debitos)
        total_creditos   = float(pago_medico.creditos)
        total_reconocido = float(pago_medico.reconocido)
        neto_a_pagar     = float(pago_medico.neto_a_pagar)
    else:
        total_bruto      = sum(l["bruto"] for l in liq_items)
        total_debitos    = sum(l["total_debitos"] for l in liq_items)
        total_creditos   = sum(l["total_creditos"] for l in liq_items)
        total_reconocido = total_bruto + total_creditos - total_debitos
        neto_a_pagar     = max(0.0, total_reconocido - float(total_deducciones))

    return {
        "medico": {
            "id": medico_id,
            "nro_socio": nro_socio,
            "nombre": lm.NOMBRE,
        },
        "recibo": {
            "id": recibo.id if recibo else None,
            "nro_recibo": recibo.nro_recibo if recibo else None,
            "emision_timestamp": recibo.emision_timestamp if recibo else None,
            "estado": recibo.estado if recibo else None,
        },
        "liquidaciones": liq_items,
        "deducciones": deducciones,
        "total_bruto": total_bruto,
        "total_debitos": total_debitos,
        "total_creditos": total_creditos,
        "total_reconocido": total_reconocido,
        "total_deducciones": float(total_deducciones),
        "neto_a_pagar": neto_a_pagar,
    }
