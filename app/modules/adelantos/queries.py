"""
Lecturas de adelantos que usan los cálculos del pago (deducciones, resumen por médico,
totales, cierre). Solo importa modelos: así `deducciones` y `pagos` pueden usarlo sin ciclos.

Un adelanto cuenta en el pago por `pago_id` y estado L (en el pago abierto) o AP (con el
pago ya cerrado), igual que los ajustes de un lote.
"""
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LiquidacionAdelanto, LiquidacionAdelantoItem

ESTADOS_EN_PAGO = ("L", "AP")


async def adelantos_por_medico_en_pago(
    db: AsyncSession, pago_id: int
) -> dict[int, dict[str, Decimal]]:
    """Honorarios, gastos e importe de los adelantos del pago, por médico (listado_medico.ID)."""
    rows = (await db.execute(
        select(
            LiquidacionAdelantoItem.medico_id,
            func.coalesce(func.sum(LiquidacionAdelantoItem.honorarios), 0),
            func.coalesce(func.sum(LiquidacionAdelantoItem.gastos), 0),
            func.coalesce(func.sum(LiquidacionAdelantoItem.importe_total), 0),
        )
        .select_from(LiquidacionAdelantoItem)
        .join(LiquidacionAdelanto, LiquidacionAdelanto.id == LiquidacionAdelantoItem.adelanto_id)
        .where(
            LiquidacionAdelanto.pago_id == pago_id,
            LiquidacionAdelanto.estado.in_(ESTADOS_EN_PAGO),
        )
        .group_by(LiquidacionAdelantoItem.medico_id)
    )).all()
    return {
        int(m): {"honorarios": Decimal(h or 0), "gastos": Decimal(g or 0), "importe": Decimal(i or 0)}
        for m, h, g, i in rows
    }


async def total_adelantos_del_pago(db: AsyncSession, pago_id: int) -> Decimal:
    """Suma del importe de todos los adelantos del pago."""
    total = (await db.execute(
        select(func.coalesce(func.sum(LiquidacionAdelantoItem.importe_total), 0))
        .select_from(LiquidacionAdelantoItem)
        .join(LiquidacionAdelanto, LiquidacionAdelanto.id == LiquidacionAdelantoItem.adelanto_id)
        .where(
            LiquidacionAdelanto.pago_id == pago_id,
            LiquidacionAdelanto.estado.in_(ESTADOS_EN_PAGO),
        )
    )).scalar_one()
    return Decimal(total or 0)


async def detalle_ids_de_adelantos_del_pago(db: AsyncSession, pago_id: int) -> list[int]:
    """`detalle_facturacion` de las prestaciones que paga este pago por adelantos."""
    rows = (await db.execute(
        select(LiquidacionAdelantoItem.detalle_facturacion_id)
        .select_from(LiquidacionAdelantoItem)
        .join(LiquidacionAdelanto, LiquidacionAdelanto.id == LiquidacionAdelantoItem.adelanto_id)
        .where(
            LiquidacionAdelanto.pago_id == pago_id,
            LiquidacionAdelanto.estado.in_(ESTADOS_EN_PAGO),
        )
    )).scalars().all()
    return [int(r) for r in rows]
