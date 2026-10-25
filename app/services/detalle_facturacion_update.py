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

Cada fila que cambia deja su evento en el historial (`audit_log_facturacion`).

Estas funciones SOLO hacen flush — el commit queda a cargo del llamador.
"""
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DetalleFacturacionCMC, DetalleLiquidacion, Liquidacion
from app.modules.adelantos.queries import detalle_ids_de_adelantos_del_pago
from app.modules.facturacion import historial


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
    # Las prestaciones que paga un adelanto del pago pasan igual que las de sus facturas.
    return list(rows) + await detalle_ids_de_adelantos_del_pago(db, pago_id)


async def _pasar_estado(
    db: AsyncSession, pago_id: int, desde: str, hasta: str, tipo_evento: str,
) -> int:
    ids = await _cmc_detalle_ids_del_pago(db, pago_id)
    if not ids:
        return 0
    D = DetalleFacturacionCMC
    filtro = (D.id_detalle_prestaciones.in_(ids), D.estado == desde)
    # Las filas que van a cambiar, antes del UPDATE: sólo lo que el historial necesita.
    filas = (await db.execute(
        select(D.id_detalle_prestaciones, D.cod_obr, D.periodo, D.version, D.grupo_equipo_id)
        .where(*filtro)
    )).all()
    result = await db.execute(update(D).where(*filtro).values(estado=hasta))
    await historial.registrar(db, [
        historial.evento(
            f, tipo_evento, estado_antes=desde, estado_despues=hasta,
            contexto=historial.contexto_de(f, pago_id=pago_id),
        )
        for f in filas
    ])
    await db.flush()
    return result.rowcount


async def marcar_prestaciones_liquidadas(db: AsyncSession, pago_id: int) -> int:
    """
    Al CERRAR un pago: C → L en detalle_facturacion para toda prestación CMC
    que se copió a detalle_liquidacion en alguna liquidación de este pago, o que paga un
    adelanto de este pago.

    Devuelve la cantidad de filas actualizadas.
    """
    return await _pasar_estado(db, pago_id, "C", "L", historial.LIQUIDADA)


async def revertir_prestaciones_liquidadas(db: AsyncSession, pago_id: int) -> int:
    """
    Al REABRIR (o eliminar) un pago cerrado: L → C en detalle_facturacion para
    las mismas prestaciones que marcar_prestaciones_liquidadas había marcado.

    Devuelve la cantidad de filas actualizadas.
    """
    return await _pasar_estado(db, pago_id, "L", "C", historial.LIQUIDACION_REVERTIDA)
