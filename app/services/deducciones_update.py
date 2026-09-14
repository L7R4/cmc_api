"""
Actualización de estados de Deduccion asociados a eventos de Pago (cierre / reapertura).

marcar_deducciones_aplicadas usa generado_en_pago_id (red de seguridad sobre lo
que ya hizo aplicar_deducciones_al_cierre). revertir_deducciones_al_reabrir usa
en cambio deduccion_aplicacion.pago_id como fuente de verdad de "qué se cobró
en este pago": generado_en_pago_id no alcanza a una deducción enrolada en un
pago anterior y terminada de cobrar en este (ver diagnóstico A3).

Estas funciones SOLO hacen flush — el commit queda a cargo del llamador.
"""
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Deduccion, DeduccionAplicacion


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


async def revertir_deducciones_al_reabrir(db: AsyncSession, pago_id: int) -> dict:
    """
    Al reabrir un pago: deshace exactamente lo que el cierre de ESTE pago
    registró, leyendo deduccion_aplicacion en vez de confiar en el estado
    actual o en generado_en_pago_id.

    1. Lee las filas de deduccion_aplicacion de este pago (la fuente de verdad
       de "cuánto se cobró acá", por deducción).
    2. Resta esos montos de Deduccion.monto_aplicado (el ledger real).
    3. Borra esas filas — así un re-cierre parte de cero y el
       ON DUPLICATE KEY UPDATE de _persistir_aplicaciones no duplica el cobro.
    4. Las deducciones que habían quedado 'aplicado' por este pago vuelven a
       'en_pago' para ser re-evaluadas en el próximo cierre. Las que habían
       quedado 'pendiente' (cobertura parcial) no cambian de estado — ya están
       disponibles para que auto_enrolar_pendientes las vuelva a tomar — pero
       sí quedan con el monto_aplicado corregido.

    Devuelve conteos para auditoría.
    """
    apl_rows = (await db.execute(
        select(DeduccionAplicacion.deduccion_id, DeduccionAplicacion.aplicado)
        .where(DeduccionAplicacion.pago_id == pago_id)
    )).all()

    if not apl_rows:
        return {"aplicaciones_revertidas": 0, "deducciones_revertidas": 0}

    deltas: dict[int, Decimal] = defaultdict(Decimal)
    for row in apl_rows:
        deltas[row.deduccion_id] += row.aplicado

    for ded_id, delta in deltas.items():
        await db.execute(
            update(Deduccion)
            .where(Deduccion.id == ded_id)
            .values(monto_aplicado=Deduccion.monto_aplicado - delta)
        )

    await db.execute(
        delete(DeduccionAplicacion).where(DeduccionAplicacion.pago_id == pago_id)
    )

    result = await db.execute(
        update(Deduccion)
        .where(
            Deduccion.id.in_(list(deltas.keys())),
            Deduccion.estado == "aplicado",
        )
        .values(estado="en_pago")
    )

    await db.flush()
    return {
        "aplicaciones_revertidas": len(apl_rows),
        "deducciones_revertidas": result.rowcount,
    }
