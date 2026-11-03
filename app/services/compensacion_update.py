"""
Estados de Compensacion frente a los eventos de un Pago (cierre / reapertura / baja) y el ingreso
de las cuotas al pago por mes.

  pendiente → en_pago  sola, cuando llega el mes de la cuota (enrolar_cuotas_del_mes)
  en_pago → pagada     al cerrar el pago
  pagada  → en_pago    al reabrir el pago
  en_pago/pagada → pendiente (pago_id → NULL)   al eliminar el pago (vuelven a entrar solas)

Estas funciones SOLO hacen flush — el commit queda a cargo del llamador.
"""
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Compensacion, Pago


def mes_siguiente(mes: int, anio: int) -> tuple[int, int]:
    return (1, anio + 1) if mes == 12 else (mes + 1, anio)


async def enrolar_cuotas_del_mes(db: AsyncSession, pago: Pago) -> int:
    """Pasa al pago abierto las cuotas pendientes cuyo mes ya llegó (el del pago o uno anterior).
    Si hay dos pagos en un mes, la cuota entra al primero: al cerrarlo ya está pagada.
    Las que no tienen mes (anteriores a las cuotas) siguen entrando a mano."""
    if pago.estado != "A":
        return 0
    res = await db.execute(
        update(Compensacion)
        .where(
            Compensacion.estado == "pendiente",
            Compensacion.mes_aplicar.is_not(None),
            or_(
                Compensacion.anio_aplicar < pago.anio,
                and_(Compensacion.anio_aplicar == pago.anio, Compensacion.mes_aplicar <= pago.mes),
            ),
        )
        .values(estado="en_pago", pago_id=pago.id)
    )
    await db.flush()
    return res.rowcount or 0


async def posponer_cuotas(db: AsyncSession, comp: Compensacion, pago: Pago) -> None:
    """Al quitar una cuota del pago, pasa al mes siguiente al del pago (si no, volvería a entrar
    sola) y corre las cuotas pendientes que le siguen, para que sigan siendo una por mes."""
    mes, anio = mes_siguiente(pago.mes, pago.anio)
    siguientes = (await db.execute(
        select(Compensacion).where(
            Compensacion.plan_id == comp.plan_id,
            Compensacion.cuota_nro > comp.cuota_nro,
            Compensacion.estado == "pendiente",
            Compensacion.mes_aplicar.is_not(None),
        ).order_by(Compensacion.cuota_nro)
    )).scalars().all()
    for c in [comp, *siguientes]:
        if c.mes_aplicar is None or (c.anio_aplicar, c.mes_aplicar) < (anio, mes):
            c.mes_aplicar, c.anio_aplicar = mes, anio
        mes, anio = mes_siguiente(c.mes_aplicar, c.anio_aplicar)
    await db.flush()


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
