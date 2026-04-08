"""
Servicio de Deducciones — tabla unificada (manual + automatico).
"""
import datetime
from decimal import ROUND_DOWN, Decimal
from typing import List, Optional

from sqlalchemy import and_, case, func, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Ajuste,
    Deduccion,
    DeduccionAplicacion,
    DeduccionSaldo,
    Descuentos,
    DetalleLiquidacion,
    Liquidacion,
    ListadoMedico,
    LoteAjuste,
    Pago,
    SocioDescuento,
)
from app.modules.deducciones.schemas import (
    DeduccionCreate,
    DeduccionHistorialItem,
    DeduccionHistorialPage,
    DeduccionItemEliminarResponse,
    DeduccionPorPagoResponse,
    DeduccionRead,
    DeshacerDescuentosResponse,
    TopDeudorItem,
)

TWOPLACES = Decimal("0.01")


def _advance_month(mes: int, anio: int) -> tuple[int, int]:
    if mes == 12:
        return 1, anio + 1
    return mes + 1, anio


def _floor2(value: Decimal) -> Decimal:
    return (value * 100).to_integral_value(rounding=ROUND_DOWN) / 100


async def _get_open_pago(db: AsyncSession) -> Optional[Pago]:
    return await db.scalar(select(Pago).where(Pago.estado == "A").limit(1))


async def calc_disponible_por_medico(db: AsyncSession, pago_id: int) -> dict[int, Decimal]:
    """
    Disponible por médico = bruto (DetalleLiquidacion) + créditos - débitos (lotes en 'L').
    Usa listado_medico.ID como clave (PK interna).
    """
    bruto_q = await db.execute(
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
    bruto_map = {int(m): Decimal(v or 0) for m, v in bruto_q}

    qaj = await db.execute(
        select(
            Ajuste.medico_id,
            func.coalesce(func.sum(case((Ajuste.tipo == "d", Ajuste.honorarios + Ajuste.gastos), else_=0)), 0),
            func.coalesce(func.sum(case((Ajuste.tipo == "c", Ajuste.honorarios + Ajuste.gastos), else_=0)), 0),
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

    out: dict[int, Decimal] = {}
    for k in set(bruto_map) | set(deb_map) | set(cred_map):
        out[k] = (
            bruto_map.get(k, Decimal("0"))
            - deb_map.get(k, Decimal("0"))
            + cred_map.get(k, Decimal("0"))
        )
    return out


def _es_vencida(mes: int, anio: int) -> bool:
    hoy = datetime.date.today()
    return (anio, mes) < (hoy.year, hoy.month)


async def _enrich_deduccion(db: AsyncSession, ded: Deduccion) -> DeduccionRead:
    desc = await db.scalar(select(Descuentos).where(Descuentos.id == ded.descuento_id)) if ded.descuento_id else None
    return DeduccionRead(
        id=ded.id,
        medico_id=ded.medico_id,
        descuento_id=ded.descuento_id,
        descuento_nombre=desc.nombre if desc else "",
        origen=ded.origen,
        estado=ded.estado,
        monto_total=ded.monto_total,
        monto_cuota=ded.monto_cuota,
        calculado_total=ded.calculado_total,
        cuotas_total=ded.cuotas_total,
        cuota_nro=ded.cuota_nro,
        cuotificado=ded.cuotificado,
        grupo_id=ded.grupo_id,
        mes_aplicar=ded.mes_aplicar,
        anio_aplicar=ded.anio_aplicar,
        pagador_medico_id=ded.pagador_medico_id,
        pago_id=ded.pago_id,
        created_at=ded.created_at,
    )


async def enrich_many(db: AsyncSession, deds: list[Deduccion]) -> list[DeduccionRead]:
    desc_ids = list({d.descuento_id for d in deds if d.descuento_id})
    rows = (await db.execute(select(Descuentos).where(Descuentos.id.in_(desc_ids)))).scalars().all()
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
            monto_total=ded.monto_total,
            monto_cuota=ded.monto_cuota,
            calculado_total=ded.calculado_total,
            cuotas_total=ded.cuotas_total,
            cuota_nro=ded.cuota_nro,
            cuotificado=ded.cuotificado,
            grupo_id=ded.grupo_id,
            mes_aplicar=ded.mes_aplicar,
            anio_aplicar=ded.anio_aplicar,
            pagador_medico_id=ded.pagador_medico_id,
            pago_id=ded.pago_id,
            created_at=ded.created_at,
        ))
    return result


# Keep alias used in routes.py
_enrich_programa = _enrich_deduccion


async def crear_programa(
    db: AsyncSession, payload: DeduccionCreate
) -> List[DeduccionRead]:
    n = payload.cuotas
    monto_total = Decimal(str(payload.monto_total))
    cuota_base = _floor2(monto_total / Decimal(n))
    resto = monto_total - cuota_base * n

    open_pago = await _get_open_pago(db)

    cuotificado = n > 1
    mes, anio = payload.mes_inicio, payload.anio_inicio

    creados: list[Deduccion] = []

    for i in range(1, n + 1):
        monto_cuota = cuota_base + (resto if i == n else Decimal("0"))
        monto_cuota = monto_cuota.quantize(TWOPLACES)

        if open_pago and open_pago.mes == mes and open_pago.anio == anio:
            estado = "en_pago"
            pago_id = open_pago.id
        else:
            estado = "pendiente"
            pago_id = None

        ded = Deduccion(
            medico_id=payload.medico_id,
            descuento_id=payload.descuento_id,
            origen="manual",
            estado=estado,
            pago_id=pago_id,
            monto_total=monto_total,
            monto_cuota=monto_cuota,
            calculado_total=monto_cuota,  # mirror for display
            cuotas_total=n,
            cuota_nro=i,
            cuotificado=cuotificado,
            grupo_id=None,  # set after flush
            mes_aplicar=mes,
            anio_aplicar=anio,
            pagador_medico_id=payload.pagador_medico_id,
        )
        db.add(ded)
        creados.append(ded)

        if i < n:
            mes, anio = _advance_month(mes, anio)

    await db.flush()

    if n > 1:
        grupo = creados[0].id
        for d in creados:
            d.grupo_id = grupo
        await db.flush()

    await db.commit()
    for d in creados:
        await db.refresh(d)

    return await enrich_many(db, creados)


async def auto_asignar_programas(db: AsyncSession, pago_id: int, mes: int, anio: int) -> int:
    """Al crear un pago, asigna automáticamente las deducciones manuales pendientes del mismo mes/año."""
    result = await db.execute(
        update(Deduccion)
        .where(
            Deduccion.origen == "manual",
            Deduccion.mes_aplicar == mes,
            Deduccion.anio_aplicar == anio,
            Deduccion.estado == "pendiente",
        )
        .values(estado="en_pago", pago_id=pago_id)
    )
    return result.rowcount


async def generar_programas_en_pago(db: AsyncSession, pago_id: int) -> dict:
    """
    Carga las cuotas manuales en_pago del pago a DeduccionSaldo.
    NO aplica ni marca aplicado — eso ocurre al cerrar el pago.
    """
    deds = (
        await db.execute(
            select(Deduccion).where(
                Deduccion.pago_id == pago_id,
                Deduccion.origen == "manual",
                Deduccion.estado == "en_pago",
            )
        )
    ).scalars().all()

    if not deds:
        return {"pago_id": pago_id, "procesadas": 0, "total_cargado": "0.00", "detalle": []}

    desc_ids = list({d.descuento_id for d in deds if d.descuento_id})
    desc_map = {
        d.id: d
        for d in (
            await db.execute(select(Descuentos).where(Descuentos.id.in_(desc_ids)))
        ).scalars().all()
    } if desc_ids else {}

    pairs = list({(d.medico_id, d.descuento_id) for d in deds if d.descuento_id})
    socios_rows = (
        await db.execute(
            select(SocioDescuento).where(
                SocioDescuento.medico_id.in_([x[0] for x in pairs]),
                SocioDescuento.descuento_id.in_([x[1] for x in pairs]),
            )
        )
    ).scalars().all() if pairs else []
    socio_map: dict[tuple[int, int], SocioDescuento] = {
        (s.medico_id, s.descuento_id): s for s in socios_rows
    }

    saldo_values = []
    total_cargado = Decimal("0.00")
    detalle = []

    for ded in deds:
        if ded.pagador_medico_id is not None:
            pagador_id = ded.pagador_medico_id
        else:
            socio = socio_map.get((ded.medico_id, ded.descuento_id))
            if socio and socio.pagador_medico_id is not None:
                pagador_id = socio.pagador_medico_id
            else:
                pagador_id = ded.medico_id

        monto = Decimal(str(ded.monto_cuota or ded.calculado_total))
        total_cargado += monto
        desc = desc_map.get(ded.descuento_id) if ded.descuento_id else None

        saldo_values.append({
            "medico_id": pagador_id,
            "concepto_tipo": "desc",
            "concepto_id": ded.descuento_id,
            "saldo": monto,
        })
        detalle.append({
            "deduccion_id": ded.id,
            "medico_id": ded.medico_id,
            "pagador_medico_id": pagador_id,
            "descuento_id": ded.descuento_id,
            "descuento_nombre": desc.nombre if desc else "",
            "monto_cuota": str(monto),
        })

    # Upsert saldo (acumula deuda — se aplicará al cerrar el pago)
    saldo_tbl = DeduccionSaldo.__table__
    stmt_saldo = mysql_insert(saldo_tbl).values(saldo_values)
    await db.execute(
        stmt_saldo.on_duplicate_key_update(saldo=saldo_tbl.c.saldo + stmt_saldo.inserted.saldo)
    )
    # Nota: los rows quedan en estado='en_pago'. La aplicación real ocurre al cerrar el pago.

    await db.commit()

    return {
        "pago_id": pago_id,
        "procesadas": len(deds),
        "total_cargado": str(total_cargado.quantize(TWOPLACES)),
        "detalle": detalle,
    }


async def _fetch_historial_items(
    db: AsyncSession,
    medico_id: Optional[int] = None,
    descuento_id: Optional[int] = None,
    estado: Optional[str] = None,
    origen: Optional[str] = None,
    mes_desde: Optional[int] = None,
    anio_desde: Optional[int] = None,
    mes_hasta: Optional[int] = None,
    anio_hasta: Optional[int] = None,
) -> list[DeduccionHistorialItem]:
    """
    Carga y filtra todas las deducciones según los parámetros dados.
    Retorna lista ordenada por created_at DESC, sin paginar.
    """
    stmt = (
        select(
            Deduccion,
            Descuentos.nombre.label("desc_nombre"),
            ListadoMedico.NOMBRE.label("med_nombre"),
            Pago.mes.label("pago_mes"),
            Pago.anio.label("pago_anio"),
        )
        .outerjoin(Descuentos, Descuentos.id == Deduccion.descuento_id)
        .join(ListadoMedico, ListadoMedico.ID == Deduccion.medico_id)
        .outerjoin(Pago, Pago.id == Deduccion.pago_id)
        .where(Deduccion.estado != "eliminado")
    )

    if medico_id is not None:
        stmt = stmt.where(Deduccion.medico_id == medico_id)
    if descuento_id is not None:
        stmt = stmt.where(Deduccion.descuento_id == descuento_id)
    if origen:
        stmt = stmt.where(Deduccion.origen == origen)

    rows = (await db.execute(stmt)).all()

    # Período "desde" y "hasta" como entero comparable: anio * 12 + mes
    periodo_desde = anio_desde * 12 + mes_desde if anio_desde and mes_desde else None
    periodo_hasta = anio_hasta * 12 + mes_hasta if anio_hasta and mes_hasta else None

    items: list[DeduccionHistorialItem] = []

    for row in rows:
        ded: Deduccion = row[0]
        desc_nombre: str = row[1] or ""
        med_nombre: str = row[2] or ""
        pago_mes: Optional[int] = row[3]
        pago_anio: Optional[int] = row[4]

        # Derive display estado
        est = ded.estado
        if (
            ded.origen == "manual"
            and est == "pendiente"
            and ded.mes_aplicar is not None
            and ded.anio_aplicar is not None
            and _es_vencida(ded.mes_aplicar, ded.anio_aplicar)
        ):
            est = "vencida"

        if estado and est != estado:
            continue

        # Period: manual rows store it directly; auto rows get it from the pago
        mes_periodo = ded.mes_aplicar if ded.mes_aplicar is not None else pago_mes
        anio_periodo = ded.anio_aplicar if ded.anio_aplicar is not None else pago_anio

        # Filtro de rango de período
        if periodo_desde is not None or periodo_hasta is not None:
            if mes_periodo is None or anio_periodo is None:
                continue
            p = anio_periodo * 12 + mes_periodo
            if periodo_desde is not None and p < periodo_desde:
                continue
            if periodo_hasta is not None and p > periodo_hasta:
                continue

        items.append(DeduccionHistorialItem(
            id=ded.id,
            origen=ded.origen,
            medico_id=ded.medico_id,
            medico_nombre=med_nombre,
            descuento_id=ded.descuento_id,
            descuento_nombre=desc_nombre,
            monto=ded.calculado_total,
            mes_periodo=mes_periodo,
            anio_periodo=anio_periodo,
            estado=est,
            pago_id=ded.pago_id,
            cuota_nro=ded.cuota_nro,
            cuotas_total=ded.cuotas_total,
            grupo_id=ded.grupo_id,
            created_at=ded.created_at,
        ))

    items.sort(key=lambda x: x.created_at, reverse=True)
    return items


async def get_historial_deducciones(
    db: AsyncSession,
    page: int = 1,
    size: int = 50,
    medico_id: Optional[int] = None,
    descuento_id: Optional[int] = None,
    estado: Optional[str] = None,
    origen: Optional[str] = None,
    mes_desde: Optional[int] = None,
    anio_desde: Optional[int] = None,
    mes_hasta: Optional[int] = None,
    anio_hasta: Optional[int] = None,
) -> DeduccionHistorialPage:
    """
    Vista unificada de deducciones sobre la tabla única `deducciones`.
    Estado derivado: si manual + pendiente + vencida → estado='vencida'
    Paginado, ordenado por created_at DESC.
    """
    items = await _fetch_historial_items(
        db,
        medico_id=medico_id,
        descuento_id=descuento_id,
        estado=estado,
        origen=origen,
        mes_desde=mes_desde,
        anio_desde=anio_desde,
        mes_hasta=mes_hasta,
        anio_hasta=anio_hasta,
    )

    total = len(items)
    monto_total = sum((i.monto for i in items), Decimal("0.00"))
    offset = (page - 1) * size
    page_items = items[offset: offset + size]

    return DeduccionHistorialPage(total=total, page=page, size=size, monto_total=monto_total, items=page_items)


async def get_historial_export(
    db: AsyncSession,
    medico_id: Optional[int] = None,
    descuento_id: Optional[int] = None,
    estado: Optional[str] = None,
    origen: Optional[str] = None,
    mes_desde: Optional[int] = None,
    anio_desde: Optional[int] = None,
    mes_hasta: Optional[int] = None,
    anio_hasta: Optional[int] = None,
) -> list[DeduccionHistorialItem]:
    """Devuelve todos los ítems sin paginar, para exportación."""
    return await _fetch_historial_items(
        db,
        medico_id=medico_id,
        descuento_id=descuento_id,
        estado=estado,
        origen=origen,
        mes_desde=mes_desde,
        anio_desde=anio_desde,
        mes_hasta=mes_hasta,
        anio_hasta=anio_hasta,
    )


async def get_top_deudores(db: AsyncSession, limit: int = 10) -> list[TopDeudorItem]:
    rows = (
        await db.execute(
            select(
                DeduccionSaldo.medico_id,
                func.sum(DeduccionSaldo.saldo).label("saldo_total"),
                ListadoMedico.NOMBRE.label("med_nombre"),
                ListadoMedico.NRO_SOCIO.label("nro_socio"),
            )
            .join(ListadoMedico, ListadoMedico.ID == DeduccionSaldo.medico_id)
            .where(DeduccionSaldo.saldo > 0)
            .group_by(DeduccionSaldo.medico_id, ListadoMedico.NOMBRE, ListadoMedico.NRO_SOCIO)
            .order_by(func.sum(DeduccionSaldo.saldo).desc())
            .limit(limit)
        )
    ).all()

    return [
        TopDeudorItem(
            medico_id=int(r.medico_id),
            medico_nombre=r.med_nombre or "",
            nro_socio=int(r.nro_socio),
            saldo_total=Decimal(str(r.saldo_total or 0)),
        )
        for r in rows
    ]


# =============================================================================
# Acciones unificadas — operan directamente sobre Deduccion (única tabla)
# =============================================================================

async def _ded_to_historial(db: AsyncSession, ded: Deduccion) -> DeduccionHistorialItem:
    desc = await db.scalar(select(Descuentos).where(Descuentos.id == ded.descuento_id)) if ded.descuento_id else None
    med = await db.scalar(select(ListadoMedico).where(ListadoMedico.ID == ded.medico_id))
    pago = await db.get(Pago, ded.pago_id) if ded.pago_id else None

    est = ded.estado
    if (
        ded.origen == "manual"
        and est == "pendiente"
        and ded.mes_aplicar is not None
        and ded.anio_aplicar is not None
        and _es_vencida(ded.mes_aplicar, ded.anio_aplicar)
    ):
        est = "vencida"

    mes_periodo = ded.mes_aplicar if ded.mes_aplicar is not None else (pago.mes if pago else None)
    anio_periodo = ded.anio_aplicar if ded.anio_aplicar is not None else (pago.anio if pago else None)

    return DeduccionHistorialItem(
        id=ded.id,
        origen=ded.origen,
        medico_id=ded.medico_id,
        medico_nombre=med.NOMBRE if med else "",
        descuento_id=ded.descuento_id,
        descuento_nombre=desc.nombre if desc else "",
        monto=ded.calculado_total,
        mes_periodo=mes_periodo,
        anio_periodo=anio_periodo,
        estado=est,
        pago_id=ded.pago_id,
        cuota_nro=ded.cuota_nro,
        cuotas_total=ded.cuotas_total,
        grupo_id=ded.grupo_id,
        created_at=ded.created_at,
    )


async def _assert_pago_abierto(db: AsyncSession, pago_id: int) -> None:
    pago = await db.get(Pago, pago_id)
    if pago and pago.estado == "C":
        raise ValueError("pago_cerrado")


TRANSICIONES_VALIDAS = {
    ("pendiente", "en_pago"),
    ("en_pago", "pendiente"),
    ("pendiente", "cancelado"),
    ("en_pago", "cancelado"),
    ("pendiente", "aplicado"),   # pago presencial — el médico paga en persona
    ("en_pago", "aplicado"),     # pago presencial desde en_pago (quita del pago)
    # vencida → aplicado ya está cubierta: el código mapea "vencida" a "pendiente" antes de validar
    ("aplicado", "pendiente"),   # revertir aplicado: solo si sin pago_id o pago aún abierto
    ("aplicado", "pendiente"),   # cancelar aplicado: vuelve a pendiente (solo si sin pago_id o pago abierto)
}


async def cambiar_estado_item(
    db: AsyncSession, id: int, nuevo_estado: str
) -> DeduccionHistorialItem:
    ded = await db.get(Deduccion, id)
    if not ded:
        raise ValueError("not_found")

    actual = ded.estado
    if actual == nuevo_estado:
        raise ValueError("mismo_estado")
    if actual == "cancelado":
        raise ValueError(f"no_editable:{actual}")
    if actual == "aplicado":
        if nuevo_estado == "pendiente":
            # Permitido si: sin pago_id, o pago todavía abierto
            if ded.pago_id:
                pago = await db.get(Pago, ded.pago_id)
                if not pago or pago.estado != "A":
                    raise ValueError("pago_cerrado")
        else:
            raise ValueError(f"no_editable:{actual}")

    db_actual = "pendiente" if actual == "vencida" else actual
    if (db_actual, nuevo_estado) not in TRANSICIONES_VALIDAS:
        raise ValueError(f"transicion_invalida:{db_actual}:{nuevo_estado}")

    if nuevo_estado == "en_pago":
        pago = await _get_open_pago(db)
        if not pago:
            raise ValueError("no_pago_abierto")
        ded.pago_id = pago.id
    elif nuevo_estado == "aplicado":
        # Pago presencial: marcar como aplicado sin pasar por pago
        ded.pago_id = None
    else:
        if ded.pago_id:
            await _assert_pago_abierto(db, ded.pago_id)
        ded.pago_id = None

    ded.estado = nuevo_estado
    await db.commit()
    await db.refresh(ded)
    return await _ded_to_historial(db, ded)


async def cambiar_monto_item(
    db: AsyncSession, id: int, monto: Decimal
) -> DeduccionHistorialItem:
    ded = await db.get(Deduccion, id)
    if not ded:
        raise ValueError("not_found")
    if ded.estado in ("aplicado", "cancelado"):
        raise ValueError(f"no_editable:{ded.estado}")
    if ded.pago_id:
        await _assert_pago_abierto(db, ded.pago_id)

    monto = monto.quantize(TWOPLACES)
    ded.calculado_total = monto
    if ded.origen == "manual":
        ded.monto_cuota = monto

    await db.commit()
    await db.refresh(ded)
    return await _ded_to_historial(db, ded)


async def eliminar_item(
    db: AsyncSession, id: int
) -> DeduccionItemEliminarResponse:
    ded = await db.get(Deduccion, id)
    if not ded:
        raise ValueError("not_found")
    if ded.estado == "aplicado" and ded.pago_id:
        # Aplicado con pago cerrado → no se puede eliminar
        pago = await db.get(Pago, ded.pago_id)
        if not pago or pago.estado != "A":
            raise ValueError("no_editable:aplicado")
    elif ded.estado not in ("aplicado",) and ded.pago_id:
        await _assert_pago_abierto(db, ded.pago_id)

    if ded.origen == "manual":
        ded.estado = "eliminado"
        ded.pago_id = None
        await db.commit()
        return DeduccionItemEliminarResponse(id=id, origen="manual", estado="eliminado")
    else:
        await db.delete(ded)
        await db.commit()
        return DeduccionItemEliminarResponse(id=id, origen="automatico", estado="eliminado")


# =============================================================================
# Aplicar deducciones al cierre del pago
# =============================================================================

async def aplicar_deducciones_al_cierre(db: AsyncSession, pago_id: int) -> dict:
    """
    Se llama al cerrar el pago. Procesa todas las Deduccion en estado 'en_pago'.

    Por cada médico (usando el pagador real):
    - Calcula disponible = bruto - débitos + créditos
    - Ordena sus deducciones de mayor a menor monto
    - Aplica las que entren (greedy): DeduccionAplicacion, DeduccionSaldo -
    - Las que no entran: estado='pendiente', pago_id=None (saldo queda para el próximo período)
    """
    from collections import defaultdict

    # Todas las deducciones en_pago de este pago
    deds = (
        await db.execute(
            select(Deduccion).where(
                Deduccion.pago_id == pago_id,
                Deduccion.estado == "en_pago",
            )
        )
    ).scalars().all()

    if not deds:
        return {"pago_id": pago_id, "aplicadas": 0, "pendientes": 0, "total_aplicado": "0.00"}

    disponible_map = await calc_disponible_por_medico(db, pago_id)

    # Agrupar por pagador efectivo
    by_pagador: dict[int, list[Deduccion]] = defaultdict(list)
    for ded in deds:
        pagador = ded.pagador_medico_id if ded.pagador_medico_id is not None else ded.medico_id
        by_pagador[pagador].append(ded)

    ids_aplicados: list[int] = []
    ids_pendientes: list[int] = []
    apl_values: list[dict] = []
    saldo_deductions: list[tuple[int, int, Decimal]] = []  # (pagador_id, concepto_id, monto)
    total_aplicado = Decimal("0.00")

    for pagador_id, pagador_deds in by_pagador.items():
        disponible = disponible_map.get(pagador_id, Decimal("0"))
        # Mayor monto primero
        sorted_deds = sorted(pagador_deds, key=lambda d: d.calculado_total, reverse=True)

        restante = disponible
        for ded in sorted_deds:
            monto = Decimal(str(ded.calculado_total))
            if monto <= Decimal("0"):
                ids_pendientes.append(ded.id)
                continue

            if monto <= restante:
                # Cabe — se aplica
                apl_values.append({
                    "pago_id": pago_id,
                    "medico_id": pagador_id,
                    "concepto_tipo": "desc",
                    "concepto_id": ded.descuento_id,
                    "aplicado": monto,
                })
                saldo_deductions.append((pagador_id, ded.descuento_id, monto))
                ids_aplicados.append(ded.id)
                restante -= monto
                total_aplicado += monto
            else:
                # No cabe — vuelve a pendiente, el saldo queda para el próximo período
                ids_pendientes.append(ded.id)

    # Escribir DeduccionAplicacion
    if apl_values:
        apl_tbl = DeduccionAplicacion.__table__
        stmt_apl = mysql_insert(apl_tbl).values(apl_values)
        await db.execute(
            stmt_apl.on_duplicate_key_update(
                aplicado=apl_tbl.c.aplicado + stmt_apl.inserted.aplicado
            )
        )
        # Decrementar DeduccionSaldo por lo aplicado
        for pagador_id, concepto_id, monto in saldo_deductions:
            await db.execute(
                update(DeduccionSaldo)
                .where(
                    DeduccionSaldo.medico_id == pagador_id,
                    DeduccionSaldo.concepto_tipo == "desc",
                    DeduccionSaldo.concepto_id == concepto_id,
                )
                .values(saldo=DeduccionSaldo.saldo - monto)
            )

    # Marcar aplicadas
    if ids_aplicados:
        await db.execute(
            update(Deduccion)
            .where(Deduccion.id.in_(ids_aplicados))
            .values(estado="aplicado")
        )

    # Las que no entraron: vuelven a pendiente sin pago_id
    if ids_pendientes:
        await db.execute(
            update(Deduccion)
            .where(Deduccion.id.in_(ids_pendientes))
            .values(estado="pendiente", pago_id=None)
        )

    await db.commit()

    return {
        "pago_id": pago_id,
        "aplicadas": len(ids_aplicados),
        "pendientes": len(ids_pendientes),
        "total_aplicado": str(total_aplicado.quantize(TWOPLACES)),
    }


# =============================================================================
# Verificar existencia de deducciones para un pago
# =============================================================================

async def verificar_deducciones_por_pago(
    db: AsyncSession, pago_id: int
) -> DeduccionPorPagoResponse:
    """
    Devuelve si existen deducciones asociadas al pago_id dado,
    junto con el listado completo y el monto total.
    """
    deds = (
        await db.execute(
            select(Deduccion)
            .where(
                Deduccion.pago_id == pago_id,
                Deduccion.estado != "eliminado",
            )
            .order_by(Deduccion.id)
        )
    ).scalars().all()

    items = await enrich_many(db, list(deds))
    monto_total = sum((i.calculado_total for i in items), Decimal("0.00"))

    return DeduccionPorPagoResponse(
        existe=len(items) > 0,
        pago_id=pago_id,
        total=len(items),
        monto_total=monto_total.quantize(TWOPLACES),
        items=items,
    )


# =============================================================================
# Deshacer todos los descuentos generados automáticamente para un pago
# =============================================================================

async def deshacer_descuentos_generados(
    db: AsyncSession, pago_id: int
) -> DeshacerDescuentosResponse:
    """
    Elimina todas las Deduccion con origen='automatico' del pago indicado
    y revierte los saldos acumulados en DeduccionSaldo.
    Solo opera si el pago está abierto (estado='A').
    """
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise ValueError("pago_no_encontrado")
    if pago.estado == "C":
        raise ValueError("pago_cerrado")

    deds = (
        await db.execute(
            select(Deduccion).where(
                Deduccion.pago_id == pago_id,
                Deduccion.origen == "automatico",
                Deduccion.estado == "en_pago",
            )
        )
    ).scalars().all()

    if not deds:
        return DeshacerDescuentosResponse(
            pago_id=pago_id,
            eliminadas=0,
            monto_revertido=Decimal("0.00"),
        )

    monto_revertido = Decimal("0.00")

    for ded in deds:
        monto = Decimal(str(ded.calculado_total or 0))
        if monto > 0 and ded.descuento_id is not None:
            await db.execute(
                update(DeduccionSaldo)
                .where(
                    DeduccionSaldo.medico_id == ded.medico_id,
                    DeduccionSaldo.concepto_tipo == "desc",
                    DeduccionSaldo.concepto_id == ded.descuento_id,
                )
                .values(saldo=DeduccionSaldo.saldo - monto)
            )
        monto_revertido += monto
        await db.delete(ded)

    await db.commit()

    return DeshacerDescuentosResponse(
        pago_id=pago_id,
        eliminadas=len(deds),
        monto_revertido=monto_revertido.quantize(TWOPLACES),
    )
