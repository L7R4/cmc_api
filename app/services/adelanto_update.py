"""
Estados de LiquidacionAdelanto frente a los eventos de un Pago (cierre / reapertura / baja).

Mismas reglas que los lotes de débitos y créditos (ver lote_ajuste_update.py):
  L  = en el pago abierto
  AP = pagado, el pago ya se cerró
  L  → AP  al cerrar el pago
  AP → L   al reabrir el pago
  L/AP → C al eliminar el pago (pago_id → NULL): quedan listos para pasar a otro

Las prestaciones del adelanto pasan de C a L en `detalle_facturacion` junto con las del resto
del pago (ver detalle_facturacion_update.py).

Estas funciones SOLO hacen flush — el commit queda a cargo del llamador.
"""
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LiquidacionAdelanto


async def marcar_adelantos_aplicados(db: AsyncSession, pago_id: int) -> int:
    """Al CERRAR un pago: L → AP. Devuelve cuántos adelantos quedaron pagados."""
    res = await db.execute(
        update(LiquidacionAdelanto)
        .where(LiquidacionAdelanto.pago_id == pago_id, LiquidacionAdelanto.estado == "L")
        .values(estado="AP")
    )
    await db.flush()
    return res.rowcount


async def revertir_adelantos_al_reabrir(db: AsyncSession, pago_id: int) -> int:
    """Al REABRIR un pago: AP → L."""
    res = await db.execute(
        update(LiquidacionAdelanto)
        .where(LiquidacionAdelanto.pago_id == pago_id, LiquidacionAdelanto.estado == "AP")
        .values(estado="L")
    )
    await db.flush()
    return res.rowcount


async def rollback_adelantos_pago(db: AsyncSession, pago_id: int) -> int:
    """Al ELIMINAR un pago: L o AP → C y sin pago, disponibles para otro pago."""
    res = await db.execute(
        update(LiquidacionAdelanto)
        .where(LiquidacionAdelanto.pago_id == pago_id, LiquidacionAdelanto.estado.in_(["L", "AP"]))
        .values(pago_id=None, estado="C")
    )
    await db.flush()
    return res.rowcount
