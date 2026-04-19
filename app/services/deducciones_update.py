"""
Actualización de estados de Deduccion asociados a eventos de Pago (cierre / reapertura).

Usa generado_en_pago_id para identificar qué deducciones pertenecen a un pago.

Estas funciones SOLO hacen flush — el commit queda a cargo del llamador.
"""
from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Deduccion


async def marcar_deducciones_aplicadas(db: AsyncSession, pago_id: int) -> int:
    """
    Al cerrar un pago: marca como 'aplicado' cualquier Deduccion que haya quedado
    en estado 'en_pago' y fue generada/enrolada para este pago.

    En condiciones normales, aplicar_deducciones_al_cierre ya hace esta transición.
    Esta función actúa como red de seguridad.

    Solo aplica a deducciones paga_por_caja=False (que usan el flujo en_pago).

    Devuelve la cantidad de filas actualizadas.
    """
    result = await db.execute(
        update(Deduccion)
        .where(
            Deduccion.generado_en_pago_id == pago_id,
            Deduccion.estado == "en_pago",
        )
        .values(estado="aplicado")
    )
    await db.flush()
    return result.rowcount


async def revertir_deducciones_al_reabrir(db: AsyncSession, pago_id: int) -> int:
    """
    Al reabrir un pago: revierte las Deduccion 'aplicado' → 'en_pago' para que
    sean re-evaluadas en el próximo cierre.

    Solo revierte deducciones que fueron generadas/enroladas para este pago
    (generado_en_pago_id == pago_id), excluyendo automáticamente las
    paga_por_caja=True (que no tienen generado_en_pago_id).

    Devuelve la cantidad de filas afectadas.
    """
    result = await db.execute(
        update(Deduccion)
        .where(
            Deduccion.generado_en_pago_id == pago_id,
            Deduccion.estado == "aplicado",
        )
        .values(estado="en_pago")
    )
    await db.flush()
    return result.rowcount
