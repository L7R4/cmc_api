"""
Actualización de estados de LoteAjuste asociados a eventos de Pago (cierre / reapertura).

Semántica de estados:
  A  = Abierto       — se pueden cargar débitos/créditos sin problema
  C  = Cerrado       — no se puede cargar más nada, listo para asignar a un pago
  L  = En liquidaciones — el pago abierto tomó este lote para su proceso
  AP = Aplicado      — estuvo en un pago cerrado, inmutable; solo vuelve a C si el pago es eliminado

Transiciones válidas:
  A  → C   (cerrar lote manualmente)
  C  → A   (reabrir lote manualmente)
  C  → L   (asignar a un pago abierto)
  L  → C   (desasignar manualmente o al eliminar pago)
  L  → AP  (al cerrar el pago)
  AP → L   (al reabrir el pago)
  AP → C   (al eliminar el pago)

Estas funciones SOLO hacen flush — el commit queda a cargo del llamador.
"""
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LoteAjuste


async def marcar_lotes_aplicados(db: AsyncSession, pago_id: int) -> dict:
    """
    Al CERRAR un pago: L → AP (Aplicado).
    Los lotes quedan inmutables — no se pueden editar ni eliminar.
    Devuelve conteo por tipo para auditoría.
    """
    res_normal = await db.execute(
        update(LoteAjuste)
        .where(LoteAjuste.pago_id == pago_id, LoteAjuste.tipo == "normal", LoteAjuste.estado == "L")
        .values(estado="AP")
    )
    res_refact = await db.execute(
        update(LoteAjuste)
        .where(LoteAjuste.pago_id == pago_id, LoteAjuste.tipo == "refacturacion", LoteAjuste.estado == "L")
        .values(estado="AP")
    )
    await db.flush()
    return {
        "lotes_normal_aplicados": res_normal.rowcount,
        "lotes_refacturacion_aplicados": res_refact.rowcount,
    }


async def revertir_lotes_al_reabrir(db: AsyncSession, pago_id: int) -> dict:
    """
    Al REABRIR un pago: AP → L (vuelven a 'En liquidaciones').
    El pago sigue abierto y los lotes quedan disponibles para el próximo cierre.
    Devuelve conteo por tipo para auditoría.
    """
    res_normal = await db.execute(
        update(LoteAjuste)
        .where(LoteAjuste.pago_id == pago_id, LoteAjuste.tipo == "normal", LoteAjuste.estado == "AP")
        .values(estado="L")
    )
    res_refact = await db.execute(
        update(LoteAjuste)
        .where(LoteAjuste.pago_id == pago_id, LoteAjuste.tipo == "refacturacion", LoteAjuste.estado == "AP")
        .values(estado="L")
    )
    await db.flush()
    return {
        "lotes_normal_revertidos": res_normal.rowcount,
        "lotes_refacturacion_revertidos": res_refact.rowcount,
    }


async def resumen_lotes_del_pago(db: AsyncSession, pago_id: int) -> dict:
    """
    Conteo de LoteAjuste del pago agrupado por tipo.
    Útil como auditoría antes del cierre. No modifica datos.
    """
    rows = (await db.execute(
        select(LoteAjuste.tipo, func.count(LoteAjuste.id).label("cantidad"))
        .where(LoteAjuste.pago_id == pago_id, LoteAjuste.estado == "L")
        .group_by(LoteAjuste.tipo)
    )).all()

    return {
        "lotes_normal": next((r.cantidad for r in rows if r.tipo == "normal"), 0),
        "lotes_refacturacion": next((r.cantidad for r in rows if r.tipo == "refacturacion"), 0),
    }
