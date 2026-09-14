"""
Rollback de Deduccion y DeduccionAplicacion al eliminar un Pago.

Reglas al eliminar un pago:
- Automáticas con generado_en_pago_id == pago_id → eliminado
- Manuales: solo las que tienen efectivamente aplicaciones EN ESTE PAGO
  (deduccion_aplicacion.pago_id == pago_id) vuelven a pendiente, y solo se les
  resta lo que ese pago les había aplicado — no las cobradas en caja ni en
  otro pago (ver diagnóstico A4: antes tocaba TODAS las manuales en_pago o
  aplicado del sistema, sin importar a qué pago pertenecían).

Estas funciones SOLO hacen flush — el commit queda a cargo del llamador.
"""
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Deduccion, DeduccionAplicacion


async def rollback_deducciones_pago(db: AsyncSession, pago_id: int) -> dict:
    """
    Revierte todo lo relacionado a deducciones de un pago antes de eliminarlo.

    1. Lee (y luego elimina) DeduccionAplicacion del pago — es la fuente de
       verdad de qué deducciones y cuánto le corresponde revertir a cada una.
    2. Resta esos montos de Deduccion.monto_aplicado.
    3. Automáticas con generado_en_pago_id == pago_id → estado='eliminado'.
    4. Manuales tocadas por este pago (via deduccion_aplicacion, paso 1) que
       queden en 'en_pago' o 'aplicado' → 'pendiente'.
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

    # 3. Automáticas generadas para este pago → eliminado
    res_auto = await db.execute(
        update(Deduccion)
        .where(
            Deduccion.generado_en_pago_id == pago_id,
            Deduccion.origen == "automatico",
        )
        .values(estado="eliminado", generado_en_pago_id=None)
    )

    # 4. Manuales efectivamente tocadas por este pago → pendiente.
    #    - 'en_pago': por el invariante de "un solo pago abierto a la vez",
    #      toda manual en_pago pertenece a ESTE pago si se lo está eliminando
    #      (esto sí era correcto en el código anterior).
    #    - 'aplicado': solo las que este pago concretamente aplicó (según el
    #      paso 1) — no las cobradas en caja ni en otro pago ya cerrado, que
    #      es lo que el código anterior arrasaba sin querer (ver A4).
    res_manual = await db.execute(
        update(Deduccion)
        .where(
            Deduccion.origen == "manual",
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
        "manuales_revertidas": res_manual.rowcount,
    }
