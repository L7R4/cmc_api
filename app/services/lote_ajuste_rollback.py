"""
Rollback de LoteAjuste al eliminar un Pago.

Los lotes en estado 'L' (liquidado) vinculados al pago vuelven a estado 'C'
(cerrado/listo) y se les limpia el pago_id, quedando disponibles para ser
asignados a otro pago.

Estas funciones SOLO hacen flush — el commit queda a cargo del llamador.
"""
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LoteAjuste


async def rollback_lotes_pago(db: AsyncSession, pago_id: int) -> dict:
    """
    Al ELIMINAR un pago: desvincula todos los LoteAjuste (L o AP → C, pago_id → NULL).
    Los lotes quedan disponibles para ser asignados a otro pago.

    Devuelve un dict con conteos por tipo para auditoría.
    """
    # Cubre tanto estado='L' (pago estaba abierto) como 'AP' (pago estaba cerrado)
    res_normal = await db.execute(
        update(LoteAjuste)
        .where(
            LoteAjuste.pago_id == pago_id,
            LoteAjuste.tipo == "normal",
            LoteAjuste.estado.in_(["L", "AP"]),
        )
        .values(pago_id=None, estado="C")
    )

    res_refact = await db.execute(
        update(LoteAjuste)
        .where(
            LoteAjuste.pago_id == pago_id,
            LoteAjuste.tipo == "refacturacion",
            LoteAjuste.estado.in_(["L", "AP"]),
        )
        .values(pago_id=None, estado="C")
    )

    await db.flush()
    return {
        "lotes_normal_revertidos": res_normal.rowcount,
        "lotes_refacturacion_revertidos": res_refact.rowcount,
    }
