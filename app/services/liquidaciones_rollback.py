"""
Rollback de Liquidacion, DetalleLiquidacion y Recibo al eliminar un Pago.

Orden de eliminación respetando FK constraints:
  1. DetalleLiquidacion (FK → liquidacion, RESTRICT en guardar_atencion)
  2. Liquidacion (FK → pago, sin ondelete explícito)
  3. Recibo (FK → pago, RESTRICT) — bloqueado si hay recibos en estado 'pagado'

PagoMedico tiene ondelete='CASCADE' en pago_id, se elimina automáticamente
al borrar el Pago — no requiere acción aquí.

Estas funciones SOLO hacen flush — el commit queda a cargo del llamador.
"""
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DetalleLiquidacion, Liquidacion, Recibo


async def rollback_liquidaciones_pago(db: AsyncSession, pago_id: int) -> dict:
    """
    Elimina todas las Liquidacion y sus DetalleLiquidacion para el pago dado.

    Pasos:
      1. Obtener IDs de liquidaciones del pago.
      2. DELETE DetalleLiquidacion WHERE liquidacion_id IN (...).
      3. DELETE Liquidacion WHERE pago_id = pago_id.

    Devuelve conteos para auditoría.
    """
    # Obtener IDs de liquidaciones del pago
    liq_ids = (await db.execute(
        select(Liquidacion.id).where(Liquidacion.pago_id == pago_id)
    )).scalars().all()

    detalles_eliminados = 0
    if liq_ids:
        res_det = await db.execute(
            delete(DetalleLiquidacion).where(DetalleLiquidacion.liquidacion_id.in_(liq_ids))
        )
        detalles_eliminados = res_det.rowcount

    res_liq = await db.execute(
        delete(Liquidacion).where(Liquidacion.pago_id == pago_id)
    )

    await db.flush()
    return {
        "liquidaciones_eliminadas": res_liq.rowcount,
        "detalles_eliminados": detalles_eliminados,
    }


async def rollback_recibos_pago(db: AsyncSession, pago_id: int) -> dict:
    """
    Elimina todos los Recibo del pago.

    Precondición: el llamador debe haber validado que no hay recibos en
    estado 'pagado' antes de invocar esta función (esos representan
    pagos reales ya efectuados y no deben eliminarse silenciosamente).

    Devuelve conteo para auditoría.
    """
    res = await db.execute(
        delete(Recibo).where(Recibo.pago_id == pago_id)
    )
    await db.flush()
    return {"recibos_eliminados": res.rowcount}
