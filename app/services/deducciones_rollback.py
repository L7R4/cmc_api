"""
Rollback de Deduccion y DeduccionAplicacion al eliminar un Pago.

Reglas al eliminar un pago:
- Automáticas con generado_en_pago_id == pago_id → eliminado
- Manuales en estado en_pago o aplicado → pendiente (todas, no solo las del pago)

Además se eliminan los registros de DeduccionAplicacion con pago_id == pago_id.

Estas funciones SOLO hacen flush — el commit queda a cargo del llamador.
"""
from decimal import Decimal

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Deduccion, DeduccionAplicacion


async def rollback_deducciones_pago(db: AsyncSession, pago_id: int) -> dict:
    """
    Revierte todo lo relacionado a deducciones de un pago antes de eliminarlo.

    1. Elimina DeduccionAplicacion del pago.
    2. Automáticas con generado_en_pago_id == pago_id → estado='eliminado'.
    3. Todas las manuales en_pago/aplicado → estado='pendiente', resetear monto_aplicado.
    """
    # 1. Eliminar DeduccionAplicacion del pago
    res_apl = await db.execute(
        delete(DeduccionAplicacion).where(DeduccionAplicacion.pago_id == pago_id)
    )

    # 2. Automáticas generadas para este pago → eliminado
    res_auto = await db.execute(
        update(Deduccion)
        .where(
            Deduccion.generado_en_pago_id == pago_id,
            Deduccion.origen == "automatico",
        )
        .values(estado="eliminado", generado_en_pago_id=None)
    )

    # 3. Todas las manuales en_pago/aplicado → pendiente
    # (solo puede haber un pago abierto, por lo que todas apuntan a este pago)
    res_manual = await db.execute(
        update(Deduccion)
        .where(
            Deduccion.origen == "manual",
            Deduccion.estado.in_(["en_pago", "aplicado"]),
        )
        .values(
            estado="pendiente",
            generado_en_pago_id=None,
            monto_aplicado=Decimal("0.00"),
        )
    )

    await db.flush()
    return {
        "aplicaciones_eliminadas": res_apl.rowcount,
        "automaticas_eliminadas": res_auto.rowcount,
        "manuales_revertidas": res_manual.rowcount,
    }
