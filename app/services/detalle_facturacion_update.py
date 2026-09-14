"""
Actualización de estados de DetalleFacturacionCMC asociados a eventos de Pago
(cierre / reapertura).

Semántica de detalle_facturacion.estado (columna compartida con CMC, sin
constraint de DB — ver app/db/models/cmc_facturacion.py):
  C = Cerrado    — prestación lista para liquidar, todavía no entró a ningún pago
  L = Liquidada  — ya forma parte de una liquidación de un pago cerrado

Solo se marcan/revierten las filas fuente='cmc' que efectivamente se copiaron
a detalle_liquidacion para este pago (vía cmc_detalle_id). Las de fuente='ga'
(GuardarAtencion, flujo legacy) no usan detalle_facturacion y quedan afuera.

Estas funciones SOLO hacen flush — el commit queda a cargo del llamador.
"""
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DetalleFacturacionCMC, DetalleLiquidacion, Liquidacion


async def _cmc_detalle_ids_del_pago(db: AsyncSession, pago_id: int) -> list[int]:
    rows = (await db.execute(
        select(DetalleLiquidacion.cmc_detalle_id)
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .where(
            Liquidacion.pago_id == pago_id,
            DetalleLiquidacion.fuente == "cmc",
            DetalleLiquidacion.cmc_detalle_id.isnot(None),
        )
    )).scalars().all()
    return list(rows)


async def marcar_prestaciones_liquidadas(db: AsyncSession, pago_id: int) -> int:
    """
    Al CERRAR un pago: C → L en detalle_facturacion para toda prestación CMC
    que se copió a detalle_liquidacion en alguna liquidación de este pago.

    Devuelve la cantidad de filas actualizadas.
    """
    ids = await _cmc_detalle_ids_del_pago(db, pago_id)
    if not ids:
        return 0
    result = await db.execute(
        update(DetalleFacturacionCMC)
        .where(
            DetalleFacturacionCMC.id_detalle_prestaciones.in_(ids),
            DetalleFacturacionCMC.estado == "C",
        )
        .values(estado="L")
    )
    await db.flush()
    return result.rowcount


async def revertir_prestaciones_liquidadas(db: AsyncSession, pago_id: int) -> int:
    """
    Al REABRIR (o eliminar) un pago cerrado: L → C en detalle_facturacion para
    las mismas prestaciones que marcar_prestaciones_liquidadas había marcado.

    Devuelve la cantidad de filas actualizadas.
    """
    ids = await _cmc_detalle_ids_del_pago(db, pago_id)
    if not ids:
        return 0
    result = await db.execute(
        update(DetalleFacturacionCMC)
        .where(
            DetalleFacturacionCMC.id_detalle_prestaciones.in_(ids),
            DetalleFacturacionCMC.estado == "L",
        )
        .values(estado="C")
    )
    await db.flush()
    return result.rowcount
