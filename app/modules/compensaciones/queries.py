"""
Lecturas de compensaciones que usan los cálculos del pago (deducciones, resumen por médico,
totales, cierre). Solo importa modelos: así `deducciones` y `pagos` pueden usarlo sin ciclos.

Una compensación cuenta en el pago por `pago_id` y estado en_pago (pago abierto) o pagada (pago
cerrado). No es producción: suma o resta al reconocido y al disponible del socio, nunca a las
bases de los porcentuales.
"""
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Compensacion

ESTADOS_EN_PAGO = ("en_pago", "pagada")


def leyenda_recibo(c: Compensacion) -> str:
    """La leyenda que ve el socio; si va en cuotas, con cuál es: «Rotura de silla (cuota 2/5)»."""
    if (c.cuotas_total or 1) > 1:
        return f"{c.leyenda} (cuota {c.cuota_nro}/{c.cuotas_total})"
    return c.leyenda


async def compensaciones_por_medico_en_pago(db: AsyncSession, pago_id: int) -> dict[int, Decimal]:
    """Importe neto (con signo) de las compensaciones del pago, por médico (listado_medico.ID)."""
    rows = (await db.execute(
        select(Compensacion.medico_id, func.coalesce(func.sum(Compensacion.importe), 0))
        .where(Compensacion.pago_id == pago_id, Compensacion.estado.in_(ESTADOS_EN_PAGO))
        .group_by(Compensacion.medico_id)
    )).all()
    return {int(m): Decimal(i or 0) for m, i in rows}


async def totales_compensaciones_del_pago(db: AsyncSession, pago_id: int) -> tuple[Decimal, Decimal]:
    """(a favor, en contra) del pago: dos positivos; lo que resta se devuelve sin signo."""
    favor, contra = (await db.execute(
        select(
            func.coalesce(func.sum(func.IF(Compensacion.importe > 0, Compensacion.importe, 0)), 0),
            func.coalesce(func.sum(func.IF(Compensacion.importe < 0, -Compensacion.importe, 0)), 0),
        ).where(Compensacion.pago_id == pago_id, Compensacion.estado.in_(ESTADOS_EN_PAGO))
    )).one()
    return Decimal(favor or 0), Decimal(contra or 0)
