from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, exists, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import DetalleFacturacionCMC, FacturacionCMC, Liquidacion, LiquidacionAdelantoItem, LoteAjuste

router = APIRouter()


@router.get("/disponibles_lotes_ajustes")
async def facturas_disponibles_lotes(
    obra_social_id: int = Query(..., alias="obra_social_id"),
    anio: Optional[int] = Query(None),
    incluir_con_lote: bool = Query(
        False,
        description="Incluye también las facturas que ya tienen lote (para refacturar, "
                    "que se puede hacer varias veces sobre la misma factura).",
    ),
    db: AsyncSession = Depends(get_db),
):
    """Facturas cerradas de la obra social; por defecto, las que aún no tienen lote 'normal'.

    Una por factura: la original y cada complementaria del mismo período aparecen
    por separado, con su número, porque el lote cuelga de la factura.
    """
    subq = (
        select(LoteAjuste.id)
        .where(
            LoteAjuste.factura_id == FacturacionCMC.id_prestaciones,
            LoteAjuste.tipo == "normal",
        )
        .limit(1)
    )
    stmt = (
        select(
            FacturacionCMC.id_prestaciones,
            FacturacionCMC.periodo,
            FacturacionCMC.nro_factura,
            FacturacionCMC.tipo_factura,
            FacturacionCMC.version,
            FacturacionCMC.estado,
        )
        .where(
            FacturacionCMC.cod_obr == str(obra_social_id),
            FacturacionCMC.estado.in_(["C", "L", "LC"]),
        )
        .order_by(FacturacionCMC.periodo.desc(), FacturacionCMC.version)
    )
    if not incluir_con_lote:
        stmt = stmt.where(not_(subq.exists()))
    if anio is not None:
        stmt = stmt.where(FacturacionCMC.periodo.startswith(str(anio)))
    rows = (await db.execute(stmt)).mappings().all()
    return [
        {
            "FACTURA_ID": r["id_prestaciones"],
            "ANIO": int(r["periodo"][:4]),
            "MES": int(r["periodo"][4:]),
            "NRO_FACTURA": r["nro_factura"],
            "TIPO_FACTURA": r["tipo_factura"],
            "VERSION": r["version"],
            "ESTADO": r["estado"],
            "PERIODO": r["periodo"],
        }
        for r in rows
    ]


@router.get("/facturas_para_pago")
async def facturas_para_pago(
    obra_social_id: int = Query(..., alias="obra_social_id"),
    pago_id: Optional[int] = Query(
        None, description="Si se manda, deja afuera las facturas que ya están en ese pago."
    ),
    db: AsyncSession = Depends(get_db),
):
    """Facturas de la obra social con prestaciones cerradas todavía sin liquidar.

    Una fila por factura (la original y cada complementaria), con su número, para
    que el operador vea qué va a pagar. La cantidad y el importe son de las
    prestaciones cerradas ('C') de esa factura que entran al pago. Las que están reservadas en
    un adelanto no entran (las paga el adelanto): se informan aparte en RESERVADAS.
    """
    cod_obr = str(obra_social_id)
    D = DetalleFacturacionCMC
    reservada = (
        exists()
        .where(LiquidacionAdelantoItem.detalle_facturacion_id == D.id_detalle_prestaciones)
        .correlate(D)
    )
    pendientes = (
        select(
            D.periodo.label("periodo"),
            D.version.label("version"),
            func.sum(func.IF(reservada, 0, 1)).label("cantidad"),
            func.coalesce(func.sum(func.IF(reservada, 0, D.importe_total)), 0).label("importe"),
            func.sum(func.IF(reservada, 1, 0)).label("reservadas"),
            func.coalesce(func.sum(func.IF(reservada, D.importe_total, 0)), 0).label("importe_reservado"),
        )
        .where(DetalleFacturacionCMC.cod_obr == cod_obr, DetalleFacturacionCMC.estado == "C")
        .group_by(DetalleFacturacionCMC.periodo, DetalleFacturacionCMC.version)
        .subquery()
    )
    stmt = (
        select(
            FacturacionCMC.id_prestaciones,
            FacturacionCMC.periodo,
            FacturacionCMC.nro_factura,
            FacturacionCMC.tipo_factura,
            FacturacionCMC.version,
            FacturacionCMC.estado,
            pendientes.c.cantidad,
            pendientes.c.importe,
            pendientes.c.reservadas,
            pendientes.c.importe_reservado,
        )
        .join(
            pendientes,
            and_(
                pendientes.c.periodo == FacturacionCMC.periodo,
                pendientes.c.version == FacturacionCMC.version,
            ),
        )
        .where(
            FacturacionCMC.cod_obr == cod_obr,
            FacturacionCMC.estado.in_(["C", "L", "LC"]),
            # Una factura con todo reservado en adelantos no tiene nada para este pago.
            pendientes.c.cantidad > 0,
        )
        .order_by(FacturacionCMC.periodo.desc(), FacturacionCMC.version)
    )
    if pago_id is not None:
        ya_en_el_pago = (
            select(Liquidacion.id)
            .where(
                Liquidacion.pago_id == pago_id,
                Liquidacion.factura_id == FacturacionCMC.id_prestaciones,
            )
            .exists()
        )
        stmt = stmt.where(not_(ya_en_el_pago))
    rows = (await db.execute(stmt)).mappings().all()
    return [
        {
            "FACTURA_ID": r["id_prestaciones"],
            "ANIO": int(r["periodo"][:4]),
            "MES": int(r["periodo"][4:]),
            "NRO_FACTURA": r["nro_factura"],
            "TIPO_FACTURA": r["tipo_factura"],
            "VERSION": r["version"],
            "ESTADO": r["estado"],
            "PERIODO": r["periodo"],
            "CANTIDAD": int(r["cantidad"]),
            "IMPORTE": float(r["importe"] or 0),
            "RESERVADAS": int(r["reservadas"] or 0),
            "IMPORTE_RESERVADO": float(r["importe_reservado"] or 0),
        }
        for r in rows
    ]


@router.get("/disponibles")
async def periodos_disponibles(
    obra_social_id: int = Query(..., alias="obra_social_id"),
    anio: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Períodos con facturación CMC cerrada que todavía tienen prestaciones sin liquidar.

    Antes excluía cualquier período que ya tuviera una Liquidacion (en
    cualquier pago), así que una factura complementaria sobre un período ya
    liquidado no podía volver a ofrecerse aunque llegaran prestaciones nuevas
    (ver diagnóstico C3/A7 — build_detalles_from_cmc solo copia estado='C', y
    desde que el cierre de pago marca 'L' lo ya liquidado, "hay algo pendiente"
    se puede preguntar directamente en detalle_facturacion en vez de inferirlo
    de si existe o no una Liquidacion.
    """
    pendientes_subq = (
        select(DetalleFacturacionCMC.id_detalle_prestaciones)
        .where(
            DetalleFacturacionCMC.cod_obr == str(obra_social_id),
            DetalleFacturacionCMC.periodo == FacturacionCMC.periodo,
            DetalleFacturacionCMC.estado == "C",
        )
        .limit(1)
    )
    stmt = (
        select(
            FacturacionCMC.periodo,
            FacturacionCMC.nro_factura,
            FacturacionCMC.tipo_factura,
            FacturacionCMC.estado,
        )
        .where(
            FacturacionCMC.cod_obr == str(obra_social_id),
            FacturacionCMC.estado.in_(["C", "L", "LC"]),
            pendientes_subq.exists(),
        )
        .distinct()
        .order_by(FacturacionCMC.periodo.desc())
    )
    if anio is not None:
        stmt = stmt.where(FacturacionCMC.periodo.startswith(str(anio)))
    rows = (await db.execute(stmt)).mappings().all()
    return [
        {
            "ANIO": int(r["periodo"][:4]),
            "MES": int(r["periodo"][4:]),
            "NRO_FACTURA": r["nro_factura"],
            "TIPO_FACTURA": r["tipo_factura"],
            "ESTADO": r["estado"],
            "PERIODO": r["periodo"],
        }
        for r in rows
    ]
