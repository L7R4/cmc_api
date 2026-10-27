"""
Estados de Compensacion frente a los eventos de un Pago (cierre / reapertura / baja).

  en_pago → pagada     al cerrar el pago
  pagada  → en_pago    al reabrir el pago
  en_pago/pagada → pendiente (pago_id → NULL)   al eliminar el pago

Estas funciones SOLO hacen flush — el commit queda a cargo del llamador.
"""
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Compensacion


async def marcar_compensaciones_pagadas(db: AsyncSession, pago_id: int) -> int:
    res = await db.execute(
        update(Compensacion)
        .where(Compensacion.pago_id == pago_id, Compensacion.estado == "en_pago")
        .values(estado="pagada")
    )
    await db.flush()
    return res.rowcount


async def revertir_compensaciones_al_reabrir(db: AsyncSession, pago_id: int) -> int:
    res = await db.execute(
        update(Compensacion)
        .where(Compensacion.pago_id == pago_id, Compensacion.estado == "pagada")
        .values(estado="en_pago")
    )
    await db.flush()
    return res.rowcount


async def rollback_compensaciones_pago(db: AsyncSession, pago_id: int) -> int:
    res = await db.execute(
        update(Compensacion)
        .where(Compensacion.pago_id == pago_id, Compensacion.estado.in_(["en_pago", "pagada"]))
        .values(estado="pendiente", pago_id=None)
    )
    await db.flush()
    return res.rowcount
