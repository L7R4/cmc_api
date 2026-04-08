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
    Especialidad,
    GuardarAtencion,
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
# Poblar detalles de liquidación desde guardar_atencion
# ================================================
async def build_detalles_liquidacion(db: AsyncSession, liquidacion_id: int) -> None:
    liq = (await db.execute(
        select(Liquidacion).where(Liquidacion.id == liquidacion_id)
    )).scalars().first()
    if not liq:
        return

    anio, mes, os_id = int(liq.anio_periodo), int(liq.mes_periodo), int(liq.obra_social_id)

    rows = (await db.execute(
        select(
            GuardarAtencion.ID.label("id_atencion"),
            GuardarAtencion.NRO_SOCIO.label("medico_id"),
            GuardarAtencion.NRO_OBRA_SOCIAL.label("obra_social_id"),
            GuardarAtencion.CODIGO_PRESTACION.label("codigo_prestacion"),
            GuardarAtencion.FECHA_PRESTACION.label("fecha_prestacion"),
            GuardarAtencion.IMPORTE_COLEGIO.label("importe_colegio"),
            GuardarAtencion.VALOR_AYUDANTE.label("valor_ayudante"),
            GuardarAtencion.VALOR_AYUDANTE_2.label("valor_ayudante_2"),
            GuardarAtencion.GASTOS.label("gastos"),
            GuardarAtencion.CANTIDAD.label("cantidad"),
            GuardarAtencion.CANT_TRATAMIENTO.label("cantidad_tratamiento"),
            GuardarAtencion.AYUDANTE.label("nro_socio_ayudante"),
            GuardarAtencion.AYUDANTE_2.label("nro_socio_ayudante_2"),
        ).where(
            GuardarAtencion.NRO_OBRA_SOCIAL == os_id,
            GuardarAtencion.ANIO_PERIODO == anio,
            GuardarAtencion.MES_PERIODO == mes,
            GuardarAtencion.EXISTE == "S",
        )
    )).mappings().all()

    existing = set((await db.execute(
        select(DetalleLiquidacion.prestacion_id, DetalleLiquidacion.medico_id)
        .where(DetalleLiquidacion.liquidacion_id == liquidacion_id)
    )).all())

    observados: list[dict] = []

    for r in rows:
        piezas = _desdoblar_en_actores(dict(r))
        for p in piezas:
            key = (p["prestacion_id"], p["medico_id"])
            if key in existing:
                continue

            if not p["importe_total"] or p["importe_total"] <= 0:
                observados.append({
                    "atencion_id": p["prestacion_id"],
                    "medico_id": p["medico_id"],
                    "razon": "importe_cero_o_negativo",
                })
                continue

            detalle_item = DetalleLiquidacion(
                liquidacion_id=liq.id,
                medico_id=p["medico_id"],
                obra_social_id=os_id,
                prestacion_id=p["prestacion_id"],
                pagado=Decimal("0"),
                honorarios=p["honorarios"],
                gastos=p["gastos"],
                importe_total=p["importe_total"],
            )
            db.add(detalle_item)
            existing.add(key)

    await db.flush()

    if observados:
        print(f"[build_detalles] Observaciones en liq {liquidacion_id}: {json.dumps(observados)}")


def _desdoblar_en_actores(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    piezas: List[Dict[str, Any]] = []
    factor = int(row.get("cantidad") or 1) * int(row.get("cantidad_tratamiento") or 1)
    id_atencion = int(row["id_atencion"])

    medico_id = row.get("medico_id")
    if medico_id:
        honorarios = to_dec(row.get("importe_colegio")) * factor
        gastos = to_dec(row.get("gastos")) * factor
        importe_total = honorarios + gastos
        if importe_total > 0:
            piezas.append({
                "prestacion_id": id_atencion,
                "medico_id": int(medico_id),
                "honorarios": honorarios,
                "gastos": gastos,
                "importe_total": importe_total,
            })

    ayud1 = row.get("nro_socio_ayudante")
    if ayud1:
        honorarios = to_dec(row.get("valor_ayudante"))
        if honorarios > 0:
            piezas.append({
                "prestacion_id": id_atencion,
                "medico_id": int(ayud1),
                "honorarios": honorarios,
                "gastos": Decimal("0"),
                "importe_total": honorarios,
            })

    ayud2 = row.get("nro_socio_ayudante_2")
    if ayud2:
        honorarios = to_dec(row.get("valor_ayudante_2"))
        if honorarios > 0:
            piezas.append({
                "prestacion_id": id_atencion,
                "medico_id": int(ayud2),
                "honorarios": honorarios,
                "gastos": Decimal("0"),
                "importe_total": honorarios,
            })

    return piezas


# ================================================
# Recalcular totales de una liquidación (OS)
# ================================================
async def recalcular_totales_de_liquidacion(db: AsyncSession, liquidacion_id: int) -> None:
    liq = (await db.execute(
        select(Liquidacion).where(Liquidacion.id == liquidacion_id)
    )).scalars().first()
    if not liq:
        return

    # Bruto: suma de importes de detalles
    bruto_res = await db.execute(
        select(func.coalesce(func.sum(DetalleLiquidacion.importe_total), 0))
        .where(DetalleLiquidacion.liquidacion_id == liquidacion_id)
    )
    total_bruto = to_dec(bruto_res.scalar_one())

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
    from sqlalchemy import literal

    DL = DetalleLiquidacion
    GA = GuardarAtencion
    LM = aliased(ListadoMedico)

    NRO_AFILIADO = getattr(GA, "NRO_AFILIADO", literal(""))
    NOMBRE_AFILIADO = getattr(GA, "NOMBRE_AFILIADO", literal(""))
    MATRICULA = getattr(GA, "MATRICULA", GA.NRO_SOCIO)
    NOMBRE_SOCIO = func.coalesce(GA.NOMBRE_PRESTADOR, LM.NOMBRE).label("nombreSocio")

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
                        GA.CODIGO_PRESTACION == s,
                    )
                )
            else:
                like = f"%{s}%"
                filters.append(
                    or_(
                        LM.NOMBRE.like(like),
                        GA.CODIGO_PRESTACION.like(like),
                    )
                )

    stmt_base = (
        select(
            DL.id.label("det_id"),
            DL.medico_id.label("socio"),
            NOMBRE_SOCIO,
            MATRICULA.label("matri"),
            DL.prestacion_id.label("nroOrden"),
            GA.ID.label("atencion_id"),
            GA.FECHA_PRESTACION.label("fecha"),
            GA.CODIGO_PRESTACION.label("codigo"),
            NRO_AFILIADO.label("nroAfiliado"),
            NOMBRE_AFILIADO.label("afiliado"),
            GA.CANTIDAD.label("cantidad"),
            GA.CANT_TRATAMIENTO.label("cantidad_tratamiento"),
            GA.PORCENTAJE.label("porcentaje"),
            func.coalesce(DL.honorarios, 0).label("honorarios"),
            func.coalesce(DL.gastos, 0).label("gastos"),
            func.coalesce(DL.importe_total, 0).label("importe_total"),
            func.coalesce(DL.pagado, 0).label("pagado"),
            DL.obra_social_id.label("obra_social_id"),
        )
        .select_from(DL)
        .outerjoin(GA, DL.prestacion_id == GA.ID)
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

        cantidad = int(r.get("cantidad") or 1)
        cant_trat = int(r.get("cantidad_tratamiento") or 1)
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
            "nroOrden": r["nroOrden"],
            "fecha": str(r["fecha"]) if r["fecha"] is not None else "",
            "codigo": r["codigo"] if r["codigo"] is not None else "",
            "nroAfiliado": (r.get("nroAfiliado") or None),
            "afiliado": (r.get("afiliado") or None),
            "xCant": xCant,
            "porcentaje": float(r["porcentaje"] or 0),
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
                GuardarAtencion.CODIGO_PRESTACION.label("codigo"),
                GuardarAtencion.FECHA_PRESTACION.label("fecha"),
            )
            .select_from(Ajuste)
            .join(LoteAjuste, LoteAjuste.id == Ajuste.lote_id)
            .outerjoin(GuardarAtencion, GuardarAtencion.ID == Ajuste.id_atencion)
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
                total_d += monto
            else:
                creditos.append(entry)
                total_c += monto

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
