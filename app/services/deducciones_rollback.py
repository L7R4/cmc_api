"""
Rollback de Deduccion y DeduccionAplicacion al eliminar un Pago.

Estas funciones SOLO hacen flush — el commit queda a cargo del llamador.
"""
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Deduccion, DeduccionAplicacion


async def rollback_deducciones_pago(db: AsyncSession, pago_id: int) -> dict:
    """
    Revierte todo lo relacionado a deducciones de un pago antes de eliminarlo:

    1. Elimina todos los registros de DeduccionAplicacion del pago.
    2. Para las Deduccion vinculadas al pago:
       - Limpia pago_id → NULL
       - Revierte estado a 'pendiente' (en_pago / aplicado → pendiente)

    Devuelve un dict con conteos para auditoría.
    """
    # 1. Eliminar DeduccionAplicacion del pago
    res_apl = await db.execute(
        delete(DeduccionAplicacion).where(DeduccionAplicacion.pago_id == pago_id)
    )

    # 2. Limpiar Deduccion vinculadas al pago
    res_ded = await db.execute(
        update(Deduccion)
        .where(
            Deduccion.pago_id == pago_id,
            Deduccion.estado.in_(["en_pago", "aplicado"]),
        )
        .values(pago_id=None, estado="pendiente")
    )

    await db.flush()
    return {
        "aplicaciones_eliminadas": res_apl.rowcount,
        "deducciones_revertidas": res_ded.rowcount,
    }
