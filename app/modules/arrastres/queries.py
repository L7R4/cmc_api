"""
Saldos arrastrados («Saldo deudor de débitos», concepto nro_colegio 9900), con el mismo flujo del
sistema anterior.

Qué son: lo que un médico debe porque sus débitos superaron lo que producía en un pago. No se
pierde: queda como una deuda pendiente (una `Deduccion` del concepto 9900) y se resta en el pago
siguiente donde el médico participe.

Cómo entran en el pago (igual que el sistema anterior, que los trataba como un DÉBITO más):
  - Se restan del reconocido ANTES de calcular nada: bajan la base de las contribuciones y de las
    retenciones y bajan el disponible con el que se cobran las demás deducciones.
  - Cuentan en el total de débitos del médico y del pago.
  - NO son una deducción más: no se enrolan (`en_pago`) ni se cobran «al final».
  - Todo el arrastre se consume en el pago, alcance o no la plata. Si después de restarlo el
    reconocido sigue negativo, el cierre deja una deuda nueva por el total que falta (que ya
    incluye lo que venía), igual que el sistema anterior.
  - Un médico con arrastre participa del pago aunque no tenga producción (queda con reconocido
    negativo, sin recibo), como en la liquidación del sistema anterior.

Dónde se anota el consumo: en `DeduccionAplicacion` (pago, deducción, monto), igual que cualquier
cobro. Así reabrir o eliminar el pago lo deshace con la misma lógica de siempre y una deuda ya
consumida no se puede editar ni borrar. Esas filas NO suman a «deducciones» del pago: las sumas de
deducciones aplicadas excluyen este concepto (ver `FILTRO_NO_ARRASTRE`).

Solo importa modelos: así `deducciones` y `pagos` pueden usarlo sin ciclos.
"""
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conceptos, Deduccion, DeduccionAplicacion
from app.db.models.liquidacion import Pago

# nro_colegio del concepto «Saldo deudor de débitos» (el mismo valor que NRO_COLEGIO_SALDO_DEUDOR
# de deducciones/helpers.py, que no se puede importar acá sin un ciclo).
NRO_COLEGIO_ARRASTRE = 9900

TWOPLACES = Decimal("0.01")
CERO = Decimal("0")


async def concepto_arrastre_id(db: AsyncSession) -> Optional[int]:
    """Conceptos.id del saldo deudor (None si el catálogo todavía no lo tiene)."""
    return await db.scalar(
        select(Conceptos.id).where(Conceptos.nro_colegio == NRO_COLEGIO_ARRASTRE)
    )


def _condicion_pendiente(concepto_id: int, pago: Pago):
    """Filas del concepto que un pago abierto debe restar: deuda viva, de un período que ya
    llegó, que no se cobra en caja y con saldo."""
    return and_(
        Deduccion.descuento_id == concepto_id,
        Deduccion.estado.in_(("pendiente", "en_pago")),
        Deduccion.paga_por_caja == False,  # noqa: E712
        or_(
            Deduccion.anio_aplicar.is_(None),
            Deduccion.anio_aplicar < pago.anio,
            and_(Deduccion.anio_aplicar == pago.anio, or_(Deduccion.mes_aplicar.is_(None), Deduccion.mes_aplicar <= pago.mes)),
        ),
        Deduccion.calculado_total - Deduccion.monto_aplicado > 0,
    )


async def arrastres_por_medico_en_pago(db: AsyncSession, pago_id: int) -> dict[int, Decimal]:
    """
    Lo que cada médico arrastra en este pago, como número positivo (listado_medico.ID).

      - Pago abierto: las deudas 9900 vivas de los períodos que ya llegaron (lo que se va a restar).
      - Pago cerrado o pagado: lo que el cierre consumió (DeduccionAplicacion de este pago).
    """
    pago = await db.get(Pago, pago_id)
    concepto_id = await concepto_arrastre_id(db)
    if pago is None or concepto_id is None:
        return {}

    if pago.estado == "A":
        rows = (await db.execute(
            select(Deduccion.medico_id, func.coalesce(func.sum(Deduccion.calculado_total - Deduccion.monto_aplicado), 0))
            .where(_condicion_pendiente(concepto_id, pago))
            .group_by(Deduccion.medico_id)
        )).all()
    else:
        rows = (await db.execute(
            select(Deduccion.medico_id, func.coalesce(func.sum(DeduccionAplicacion.aplicado), 0))
            .select_from(DeduccionAplicacion)
            .join(Deduccion, Deduccion.id == DeduccionAplicacion.deduccion_id)
            .where(DeduccionAplicacion.pago_id == pago_id, Deduccion.descuento_id == concepto_id)
            .group_by(Deduccion.medico_id)
        )).all()
    return {int(m): Decimal(t or 0) for m, t in rows if Decimal(t or 0) > 0}


async def detalle_arrastres_medico(db: AsyncSession, pago_id: int, medico_id: int) -> list[dict]:
    """Las deudas que se restan a un médico en el pago, una por fila: para el recibo y la vista previa."""
    pago = await db.get(Pago, pago_id)
    concepto_id = await concepto_arrastre_id(db)
    if pago is None or concepto_id is None:
        return []

    if pago.estado == "A":
        rows = (await db.execute(
            select(Deduccion.id, Deduccion.mes_aplicar, Deduccion.anio_aplicar, Deduccion.calculado_total - Deduccion.monto_aplicado)
            .where(_condicion_pendiente(concepto_id, pago), Deduccion.medico_id == medico_id)
            .order_by(Deduccion.anio_aplicar, Deduccion.mes_aplicar, Deduccion.id)
        )).all()
    else:
        rows = (await db.execute(
            select(Deduccion.id, Deduccion.mes_aplicar, Deduccion.anio_aplicar, DeduccionAplicacion.aplicado)
            .select_from(DeduccionAplicacion)
            .join(Deduccion, Deduccion.id == DeduccionAplicacion.deduccion_id)
            .where(
                DeduccionAplicacion.pago_id == pago_id,
                Deduccion.descuento_id == concepto_id,
                Deduccion.medico_id == medico_id,
            )
            .order_by(Deduccion.anio_aplicar, Deduccion.mes_aplicar, Deduccion.id)
        )).all()
    return [
        {
            "deduccion_id": int(i),
            "periodo": f"{int(m):02d}/{a}" if m and a else "",
            "total": float(Decimal(t or 0).quantize(TWOPLACES)),
        }
        for i, m, a, t in rows
    ]


async def consumir_arrastres_al_cierre(db: AsyncSession, pago: Pago) -> dict:
    """
    Al cerrar el pago: todo el arrastre vivo se da por consumido (queda `aplicado`, con su
    DeduccionAplicacion), alcance o no la plata del médico. Si el reconocido final sigue negativo,
    `crear_saldos_deudores` deja la deuda nueva por el total (que ya incluye lo que venía).
    Va ANTES de marcar el pago como cerrado. Sin commit.
    """
    concepto_id = await concepto_arrastre_id(db)
    if concepto_id is None:
        return {"medicos": 0, "deudas": 0, "monto_total": "0.00"}

    deds = (await db.execute(
        select(Deduccion).where(_condicion_pendiente(concepto_id, pago)).order_by(Deduccion.id)
    )).scalars().all()

    total = CERO
    medicos: set[int] = set()
    for ded in deds:
        saldo = (Decimal(ded.calculado_total) - Decimal(ded.monto_aplicado)).quantize(TWOPLACES)
        if saldo <= 0:
            continue
        db.add(DeduccionAplicacion(pago_id=pago.id, deduccion_id=ded.id, aplicado=saldo))
        ded.monto_aplicado = Decimal(ded.monto_aplicado) + saldo
        ded.estado = "aplicado"
        total += saldo
        medicos.add(ded.medico_id)
    await db.flush()
    return {"medicos": len(medicos), "deudas": len(deds), "monto_total": str(total.quantize(TWOPLACES))}
