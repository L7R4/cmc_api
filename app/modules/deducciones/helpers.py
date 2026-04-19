from decimal import ROUND_HALF_UP, ROUND_DOWN, Decimal
import datetime

from alembic.environment import Optional
from fastapi import HTTPException
from sqlalchemy import case, func, select, update, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Ajuste,
    Deduccion,
    Descuentos,
    DetalleLiquidacion,
    Liquidacion,
    ListadoMedico,
    LoteAjuste,
    SocioDescuento,
)
from app.db.models.liquidacion import Pago
from app.modules.deducciones.schemas import (
    DeduccionHistorialItem,
    DeduccionRead,
    SocioDescuentoRead,
)

#region Constantes

TWOPLACES = Decimal("0.01")

# nro_colegio de descuentos con base de cálculo especial
NRO_COLEGIO_CONTRIB_HONORARIOS = 100  # base = suma de honorarios del médico
NRO_COLEGIO_CONTRIB_GASTOS = 101      # base = suma de gastos del médico

#endregion


#region Helper base de calculo de socios

async def _base_bruto_por_medico_en_pago(
    db: AsyncSession, pago_id: int
) -> dict[int, Decimal]:
    """
    Suma de DetalleLiquidacion.importe_total 
    por médico (listado_medico.ID) de un pago específico.
    """

    q = await db.execute(
        select(
            ListadoMedico.ID.label("medico_db_id"),
            func.coalesce(func.sum(DetalleLiquidacion.importe_total), 0),
        )
        .select_from(DetalleLiquidacion)
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .join(ListadoMedico, ListadoMedico.NRO_SOCIO == DetalleLiquidacion.medico_id)
        .where(Liquidacion.pago_id == pago_id)
        .group_by(ListadoMedico.ID)
    )
    return {int(med_id): Decimal(suma or 0) for med_id, suma in q}


async def _honorarios_por_medico_en_pago(
    db: AsyncSession, pago_id: int
) -> dict[int, Decimal]:
    """
    Suma de DetalleLiquidacion.honorarios 
    por médico (listado_medico.ID) de un pago específico.
    """

    q = await db.execute(
        select(
            ListadoMedico.ID.label("medico_db_id"),
            func.coalesce(func.sum(DetalleLiquidacion.honorarios), 0),
        )
        .select_from(DetalleLiquidacion)
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .join(ListadoMedico, ListadoMedico.NRO_SOCIO == DetalleLiquidacion.medico_id)
        .where(Liquidacion.pago_id == pago_id)
        .group_by(ListadoMedico.ID)
    )
    return {int(med_id): Decimal(suma or 0) for med_id, suma in q}


async def _gastos_por_medico_en_pago(
    db: AsyncSession, pago_id: int
) -> dict[int, Decimal]:
    """
    Suma de DetalleLiquidacion.gastos 
    por médico (listado_medico.ID) de un pago específico.
    """
    q = await db.execute(
        select(
            ListadoMedico.ID.label("medico_db_id"),
            func.coalesce(func.sum(DetalleLiquidacion.gastos), 0),
        )
        .select_from(DetalleLiquidacion)
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .join(ListadoMedico, ListadoMedico.NRO_SOCIO == DetalleLiquidacion.medico_id)
        .where(Liquidacion.pago_id == pago_id)
        .group_by(ListadoMedico.ID)
    )
    return {int(med_id): Decimal(suma or 0) for med_id, suma in q}


async def _ajustes_por_medico_en_pago(
    db: AsyncSession, pago_id: int
) -> tuple[dict[int, Decimal], dict[int, Decimal]]:
    """
    Débitos y créditos de ajustes liquidados (estado='L') por médico.
    Devuelve (deb_map, cred_map) donde cada valor es la suma de
    honorarios + gastos del ajuste para ese médico.
    """
    qaj = await db.execute(
        select(
            Ajuste.medico_id,
            func.coalesce(
                func.sum(case((Ajuste.tipo == "d", Ajuste.honorarios + Ajuste.gastos), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((Ajuste.tipo == "c", Ajuste.honorarios + Ajuste.gastos), else_=0)), 0
            ),
        )
        .select_from(Ajuste)
        .join(LoteAjuste, LoteAjuste.id == Ajuste.lote_id)
        .where(LoteAjuste.pago_id == pago_id, LoteAjuste.estado == "L")
        .group_by(Ajuste.medico_id)
    )
    deb_map: dict[int, Decimal] = {}
    cred_map: dict[int, Decimal] = {}
    for med, deb, cred in qaj:
        deb_map[int(med)] = Decimal(deb or 0)
        cred_map[int(med)] = Decimal(cred or 0)
    return deb_map, cred_map


async def _disponible_por_medico_en_pago(
    db: AsyncSession, pago_id: int
) -> dict[int, Decimal]:
    """
    Disponible neto por médico = bruto − débitos de ajustes + créditos de ajustes.
    Combina _base_bruto_por_medico_en_pago y _ajustes_por_medico_en_pago.
    """
    bruto_map = await _base_bruto_por_medico_en_pago(db, pago_id)
    deb_map, cred_map = await _ajustes_por_medico_en_pago(db, pago_id)

    out: dict[int, Decimal] = {}
    for k in set(bruto_map) | set(deb_map) | set(cred_map):
        out[k] = (
            bruto_map.get(k, Decimal("0"))
            - deb_map.get(k, Decimal("0"))
            + cred_map.get(k, Decimal("0"))
        )
    return out

#endregion 


#region Helpers de descuentos

async def _get_descuento(db: AsyncSession, desc_id: int) -> Descuentos:
    """
    
    Devuelve el Descuento o lanza ValueError si no existe.
    
    """
    obj = await db.scalar(select(Descuentos).where(Descuentos.id == desc_id))
    if not obj:
        raise ValueError("Descuento inexistente")
    return obj


async def _medicos_para_descuento(db: AsyncSession, desc_id: int) -> list[tuple[int, bool]]:
    """
    Todos los médicos asignados al descuento (sin fecha_baja)
    """
    res = await db.execute(
        select(SocioDescuento.medico_id, SocioDescuento.paga_por_caja).where(
            SocioDescuento.descuento_id == desc_id,
            SocioDescuento.fecha_baja.is_(None),
        )
    )
    return list({(m, bool(p)) for m, p in res.all()})


def _calc_monto(
    p_base: Decimal, precio: Decimal | None, porcentaje: Decimal | None
) -> tuple[Decimal, Decimal | None]:
    """
    
    Calcula el monto a descontar según porcentaje o precio fijo.
    Prioridad: porcentaje > precio fijo.
      - Si el descuento tiene porcentaje → monto = base * porcentaje / 100
      - Si tiene precio fijo → monto = precio
      - Si ambos son 0 → monto = 0 (el médico se omite en bulk_generar_descuento)
    Devuelve (monto, porcentaje_usado_o_None).
    
    """
    if porcentaje and porcentaje > 0:
        m = (p_base * porcentaje / Decimal(100)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
        return (m, porcentaje)
    if precio and precio > 0:
        p = precio.quantize(TWOPLACES, rounding=ROUND_HALF_UP)
        return (p, None)
    return (Decimal(0), None)
#endregion ---------------------------------------------------------------------------


#region Enrich — conversión de modelos ORM a schemas de respuesta

async def _enrich_socio(db: AsyncSession, socio: SocioDescuento) -> SocioDescuentoRead:
    """
    
    Construye SocioDescuentoRead enriquecido con datos del médico, pagador y descuento.
    
    """
    medico = await db.get(ListadoMedico, socio.medico_id)
    desc = await db.get(Descuentos, socio.descuento_id)
    pagador = await db.get(ListadoMedico, socio.pagador_medico_id) if socio.pagador_medico_id else None

    return SocioDescuentoRead(
        id=socio.id,
        medico_id=socio.medico_id,
        medico_nombre=getattr(medico, "NOMBRE", None),
        medico_nro_socio=getattr(medico, "NRO_SOCIO", None),
        descuento_id=socio.descuento_id,
        descuento_nombre=getattr(desc, "nombre", None),
        descuento_precio=getattr(desc, "precio", None),
        descuento_porcentaje=getattr(desc, "porcentaje", None),
        pagador_medico_id=socio.pagador_medico_id,
        pagador_nombre=getattr(pagador, "NOMBRE", None),
        pagador_nro_socio=getattr(pagador, "NRO_SOCIO", None),
        fecha_alta=socio.fecha_alta,
        fecha_baja=socio.fecha_baja,
        paga_por_caja=socio.paga_por_caja,
    )


async def _enrich_deduccion(db: AsyncSession, ded: Deduccion) -> DeduccionRead:
    """
    
    Resuelve el nombre del descuento con una query individual.
    Usar para respuestas de un solo ítem (crear, patch).
    Para listas usar enrich_many (una sola query batch).
    
    """
    desc = await db.scalar(select(Descuentos).where(Descuentos.id == ded.descuento_id)) if ded.descuento_id else None
    return DeduccionRead(
        id=ded.id,
        medico_id=ded.medico_id,
        descuento_id=ded.descuento_id,
        descuento_nombre=desc.nombre if desc else "",
        origen=ded.origen,
        estado=ded.estado,
        paga_por_caja=bool(ded.paga_por_caja),
        monto_total=ded.monto_total,
        monto_cuota=ded.monto_cuota,
        calculado_total=ded.calculado_total,
        monto_aplicado=ded.monto_aplicado,
        cuotas_total=ded.cuotas_total,
        cuota_nro=ded.cuota_nro,
        cuotificado=ded.cuotificado,
        mes_aplicar=ded.mes_aplicar,
        anio_aplicar=ded.anio_aplicar,
        pagador_medico_id=ded.pagador_medico_id,
        generado_en_pago_id=ded.generado_en_pago_id,
        created_at=ded.created_at,
    )


async def enrich_many(db: AsyncSession, deds: list[Deduccion]) -> list[DeduccionRead]:
    """
    
    Carga todos los nombres de descuento de una vez (IN clause) en lugar de
    una query por ítem. Usar siempre que se devuelva más de un ítem.
    
    """
    desc_ids = list({d.descuento_id for d in deds if d.descuento_id})
    rows = (await db.execute(select(Descuentos).where(Descuentos.id.in_(desc_ids)))).scalars().all() if desc_ids else []
    nombre_map = {d.id: d.nombre for d in rows}
    result = []
    for ded in deds:
        result.append(DeduccionRead(
            id=ded.id,
            medico_id=ded.medico_id,
            descuento_id=ded.descuento_id,
            descuento_nombre=nombre_map.get(ded.descuento_id, "") if ded.descuento_id else "",
            origen=ded.origen,
            estado=ded.estado,
            paga_por_caja=bool(ded.paga_por_caja),
            monto_total=ded.monto_total,
            monto_cuota=ded.monto_cuota,
            calculado_total=ded.calculado_total,
            monto_aplicado=ded.monto_aplicado,
            cuotas_total=ded.cuotas_total,
            cuota_nro=ded.cuota_nro,
            cuotificado=ded.cuotificado,
            mes_aplicar=ded.mes_aplicar,
            anio_aplicar=ded.anio_aplicar,
            pagador_medico_id=ded.pagador_medico_id,
            generado_en_pago_id=ded.generado_en_pago_id,
            created_at=ded.created_at,
        ))
    return result


async def _ded_to_historial(db: AsyncSession, ded: Deduccion) -> DeduccionHistorialItem:
    """
    
    Convierte una Deduccion a DeduccionHistorialItem (vista enriquecida).
    A diferencia de _enrich_deduccion, este schema incluye:
      - nombre del médico (join a ListadoMedico)
      - saldo_pendiente calculado (calculado_total − monto_aplicado)
      - estado derivado: si la deducción manual está vencida (período pasado
        y aún pendiente), devuelve estado='vencida' en lugar de 'pendiente'
    Usado por la lista de deducciones paginado, el export y los endpoints de detalle.
    
    """
    row = (await db.execute(
        select(
            Descuentos.nombre.label("desc_nombre"),
            ListadoMedico.NOMBRE.label("med_nombre"),
        )
        .select_from(ListadoMedico)
        .outerjoin(Descuentos, Descuentos.id == ded.descuento_id)
        .where(ListadoMedico.ID == ded.medico_id)
        .limit(1)
    )).first()

    desc_nombre = row.desc_nombre if row else ""
    med_nombre = row.med_nombre if row else ""

    est = ded.estado
    if (
        ded.origen == "manual"
        and est == "pendiente"
        and ded.mes_aplicar is not None
        and ded.anio_aplicar is not None
        and _es_vencida(ded.mes_aplicar, ded.anio_aplicar)
    ):
        est = "vencida"

    return DeduccionHistorialItem(
        id=ded.id,
        origen=ded.origen,
        paga_por_caja=bool(ded.paga_por_caja),
        medico_id=ded.medico_id,
        medico_nombre=med_nombre,
        descuento_id=ded.descuento_id,
        descuento_nombre=desc_nombre,
        monto=ded.calculado_total,
        saldo_pendiente=ded.calculado_total - ded.monto_aplicado,
        mes_periodo=ded.mes_aplicar,
        anio_periodo=ded.anio_aplicar,
        estado=est,
        cuota_nro=ded.cuota_nro,
        cuotas_total=ded.cuotas_total,
        generado_en_pago_id=ded.generado_en_pago_id,
        created_at=ded.created_at,
    )

#endregion


#region Manejo de errores de ítems de deducción

_ERRORES_ITEM = {
    "not_found":              (404, "Ítem no encontrado"),
    "mismo_estado":           (409, "El ítem ya está en ese estado"),
    "pago_cerrado":           (409, "El pago asociado está cerrado"),
    "no_pago_abierto":        (409, "No hay pago abierto al que asignar"),
    "no_editable:aplicado":   (409, "El ítem ya fue aplicado y no puede modificarse"),
    "no_editable:cancelado":  (409, "El ítem está cancelado y no puede modificarse"),
}


def _handle_item_error(exc: ValueError) -> None:
    """Convierte un ValueError del service layer en HTTPException con código apropiado."""
    msg = str(exc)
    entry = _ERRORES_ITEM.get(msg)
    if entry is None:
        for key, val in _ERRORES_ITEM.items():
            if msg.startswith(key):
                entry = val
                break
    if entry:
        raise HTTPException(status_code=entry[0], detail=entry[1])
    raise HTTPException(status_code=400, detail=msg)

#endregion


#region Helpers pequeños y específicos usados en service.py 

def _advance_month(mes: int, anio: int) -> tuple[int, int]:
    if mes == 12:
        return 1, anio + 1
    return mes + 1, anio


def _floor2(value: Decimal) -> Decimal:
    return (value * 100).to_integral_value(rounding=ROUND_DOWN) / 100


def _es_vencida(mes: int, anio: int) -> bool:
    hoy = datetime.date.today()
    return (anio, mes) < (hoy.year, hoy.month)


async def _get_open_pago(db: AsyncSession) -> Optional[Pago]:
    return await db.scalar(select(Pago).where(Pago.estado == "A").limit(1))


async def marcar_deducciones_dirty(db: AsyncSession, pago_id: int) -> None:
    """Marca que algo cambió en el pago y las deducciones en_pago pueden estar desactualizadas."""
    await db.execute(
        update(Pago).where(Pago.id == pago_id).values(deducciones_dirty=True)
    )
#endregion 

