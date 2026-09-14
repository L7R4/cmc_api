from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import DetalleFacturacionCMC, FacturacionCMC, LoteAjuste

router = APIRouter()


@router.get("/disponibles_lotes_ajustes")
async def periodos_disponibles_lotes(
    obra_social_id: int = Query(..., alias="obra_social_id"),
    anio: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Períodos con facturación CMC cerrada que aún no tienen lote 'normal' creado."""
    subq = (
        select(LoteAjuste.id)
        .where(
            LoteAjuste.obra_social_id == obra_social_id,
            LoteAjuste.anio_periodo == FacturacionCMC.periodo.op("DIV")(100),
            LoteAjuste.mes_periodo == FacturacionCMC.periodo.op("%")(100),
            LoteAjuste.tipo == "normal",
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
            not_(subq.exists()),
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
