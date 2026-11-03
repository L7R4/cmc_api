"""
Rollback de Deduccion y DeduccionAplicacion al eliminar un Pago.

Reglas al eliminar un pago:
- Las automáticas creadas para este pago → eliminado.
- Las demás que el pago tomó (en_pago) o cobró (deduccion_aplicacion.pago_id ==
  pago_id), manuales o automáticas, vuelven a pendiente, y solo se les resta lo
  que ese pago les había aplicado — no las cobradas en caja ni en otro pago (ver
  diagnóstico A4). Antes sólo volvían las manuales, y las cuotas de la migración
  (todas «automatico») quedaban en_pago y aparecían en el pago siguiente.

Estas funciones SOLO hacen flush — el commit queda a cargo del llamador.
"""
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Deduccion, DeduccionAplicacion, Pago


async def rollback_deducciones_pago(db: AsyncSession, pago_id: int) -> dict:
    """
    Revierte todo lo relacionado a deducciones de un pago antes de eliminarlo.

    1. Lee (y luego elimina) DeduccionAplicacion del pago — es la fuente de
       verdad de qué deducciones y cuánto le corresponde revertir a cada una.
    2. Resta esos montos de Deduccion.monto_aplicado.
    3. Automáticas creadas para este pago → estado='eliminado'.
    4. Las demás en 'en_pago', y las 'aplicado' que cobró este pago (paso 1),
       manuales o automáticas → 'pendiente'.
    """
    apl_rows = (await db.execute(
        select(DeduccionAplicacion.deduccion_id, DeduccionAplicacion.aplicado)
        .where(DeduccionAplicacion.pago_id == pago_id)
    )).all()

    deltas: dict[int, Decimal] = defaultdict(Decimal)
    for row in apl_rows:
        deltas[row.deduccion_id] += row.aplicado

    # 1. Eliminar DeduccionAplicacion del pago
    res_apl = await db.execute(
        delete(DeduccionAplicacion).where(DeduccionAplicacion.pago_id == pago_id)
    )

    # 2. Restar lo que este pago había aplicado (y solo eso) del ledger real
    for ded_id, delta in deltas.items():
        await db.execute(
            update(Deduccion)
            .where(Deduccion.id == ded_id)
            .values(monto_aplicado=Deduccion.monto_aplicado - delta)
        )

    # 3. Las creadas para este pago → eliminado: las que generó (porcentuales, «Generar», Ganancias)
    #    y las porcentuales que el socio paga por caja, que nacen pendientes sin generado_en_pago_id
    #    y se reconocen por cuota_nro = id del pago. Una cuota de la migración (también
    #    «automatico») que se pasó a mano al pago quedaba marcada con su id: es anterior al pago,
    #    así que no entra acá y el paso 4 la devuelve a pendiente.
    pago_creado = await db.scalar(select(Pago.created_at).where(Pago.id == pago_id))
    res_auto = await db.execute(
        update(Deduccion)
        .where(
            Deduccion.origen == "automatico",
            or_(
                and_(Deduccion.generado_en_pago_id == pago_id, Deduccion.created_at >= pago_creado),
                and_(
                    Deduccion.generado_en_pago_id.is_(None),
                    Deduccion.cuota_nro == pago_id,
                    Deduccion.porcentaje_aplicado > 0,
                    Deduccion.estado.in_(["pendiente", "en_pago"]),
                ),
            ),
        )
        .values(estado="eliminado", generado_en_pago_id=None)
    )

    # 4. Las que este pago tomó sin haberlas creado vuelven a pendiente, sean manuales o
    #    automáticas (las cuotas y deudas de la migración son todas «automatico»):
    #    - 'en_pago': por el invariante de "un solo pago abierto a la vez", toda deducción
    #      en_pago pertenece a ESTE pago si se lo está eliminando.
    #    - 'aplicado': solo las que este pago concretamente aplicó (según el paso 1) — no las
    #      cobradas en caja ni en otro pago ya cerrado (ver A4).
    res_revertidas = await db.execute(
        update(Deduccion)
        .where(
            or_(
                Deduccion.estado == "en_pago",
                and_(Deduccion.estado == "aplicado", Deduccion.id.in_(list(deltas.keys()))),
            ),
        )
        .values(
            estado="pendiente",
            generado_en_pago_id=None,
        )
    )

    await db.flush()
    return {
        "aplicaciones_eliminadas": res_apl.rowcount,
        "automaticas_eliminadas": res_auto.rowcount,
        "revertidas_a_pendiente": res_revertidas.rowcount,
    }
