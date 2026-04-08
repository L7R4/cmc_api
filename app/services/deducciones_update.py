"""
Actualización de estados de Deduccion asociados a eventos de Pago (cierre / reapertura).

Estas funciones SOLO hacen flush — el commit queda a cargo del llamador.
"""
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Deduccion


async def marcar_deducciones_aplicadas(db: AsyncSession, pago_id: int) -> int:
    """
    Al cerrar un pago: marca como 'aplicado' cualquier Deduccion que haya quedado
    en estado 'en_pago' para este pago.

    En condiciones normales, aplicar_deducciones_al_cierre ya hace esta transición
    durante el proceso greedy. Esta función actúa como red de seguridad para rows
    que pudieran haber escapado a ese proceso (p. ej. deducciones con monto=0 que
    fueron omitidas pero siguen vinculadas al pago).

    Devuelve la cantidad de filas actualizadas.
    """
    result = await db.execute(
        update(Deduccion)
        .where(Deduccion.pago_id == pago_id, Deduccion.estado == "en_pago")
        .values(estado="aplicado")
    )
    await db.flush()
    return result.rowcount


async def revertir_deducciones_al_reabrir(db: AsyncSession, pago_id: int) -> int:
    """
    Al reabrir un pago: revierte las Deduccion 'aplicado' → 'en_pago' para que
    sean re-evaluadas en el próximo cierre.

    No toca DeduccionAplicacion ni DeduccionSaldo — esos se recalculan en el
    siguiente aplicar_deducciones_al_cierre.

    Devuelve la cantidad de filas afectadas.
    """
    result = await db.execute(
        update(Deduccion)
        .where(Deduccion.pago_id == pago_id, Deduccion.estado == "aplicado")
        .values(estado="en_pago")
    )
    await db.flush()
    return result.rowcount
