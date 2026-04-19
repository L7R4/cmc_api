from collections import defaultdict
from decimal import ROUND_DOWN, Decimal
from typing import List, Optional

from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Ajuste,
    Deduccion,
    DeduccionAplicacion,
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
    DeduccionesAplicadasResponse,
    DeshacerDescuentosResponse,
    TopDeudorItem,
)

from app.modules.deducciones.helpers import (
    _advance_month,
    _base_bruto_por_medico_en_pago,
    _calc_monto,
    _ded_to_historial,
    _disponible_por_medico_en_pago,
    _enrich_deduccion,
    _es_vencida,
    _floor2,
    _gastos_por_medico_en_pago,
    _get_open_pago,
    _honorarios_por_medico_en_pago,
    _medicos_para_descuento,
    enrich_many,
    marcar_deducciones_dirty,
    NRO_COLEGIO_CONTRIB_GASTOS,
    NRO_COLEGIO_CONTRIB_HONORARIOS,
)


TWOPLACES = Decimal("0.01")


# =============================================================================
# Auto-enrolamiento de deducciones pendientes (manual + automático)
# =============================================================================
async def auto_enrolar_pendientes(db: AsyncSession, pago: Pago) -> dict:
    """
    Marca como en_pago todas las deducciones (manual + automático) con
    paga_por_caja=False que estén pendientes y cuyo período <= pago.mes/anio.
    Devuelve {"cantidad": int, "ids": list[int]}.
    """
    filtro = and_(
        Deduccion.paga_por_caja == False,
        Deduccion.estado == "pendiente",
        or_(
            Deduccion.anio_aplicar < pago.anio,
            and_(
                Deduccion.anio_aplicar == pago.anio,
                Deduccion.mes_aplicar <= pago.mes,
            ),
        ),
    )
    ids: list[int] = list((await db.execute(select(Deduccion.id).where(filtro))).scalars().all())
    if ids:
        await db.execute(
            update(Deduccion)
            .where(Deduccion.id.in_(ids))
            .values(estado="en_pago", generado_en_pago_id=pago.id)
        )
    return {"cantidad": len(ids), "ids": ids}


# =============================================================================
# Refrescar deducciones del pago abierto
# =============================================================================

async def recalcular_automaticas_porcentuales(db: AsyncSession, pago_id: int) -> dict:
    """
    Recalcula calculado_total de las deducciones automáticas en_pago que usan porcentaje.
    Determina la base correcta según nro_colegio del descuento:
      - NRO_COLEGIO_CONTRIB_HONORARIOS → base = honorarios del médico
      - NRO_COLEGIO_CONTRIB_GASTOS     → base = gastos del médico
      - resto                          → base = importe_total (bruto)
    Devuelve {"cantidad": int, "ids": list[int]} con las deducciones cuyo calculado_total cambió.
    """
    deds_auto = (await db.execute(
        select(Deduccion).where(
            Deduccion.origen == "automatico",
            Deduccion.estado == "en_pago",
            Deduccion.porcentaje_aplicado > 0,
        )
    )).scalars().all()

    if not deds_auto:
        return {"cantidad": 0, "ids": []}

    desc_ids = list({d.descuento_id for d in deds_auto if d.descuento_id})
    desc_rows = (await db.execute(
        select(Descuentos).where(Descuentos.id.in_(desc_ids))
    )).scalars().all()
    desc_map = {d.id: d for d in desc_rows}

    bruto_map = await _base_bruto_por_medico_en_pago(db, pago_id)
    hon_map = await _honorarios_por_medico_en_pago(db, pago_id)
    gas_map = await _gastos_por_medico_en_pago(db, pago_id)

    ids_recalculadas: list[int] = []
    for ded in deds_auto:
        if not ded.descuento_id:
            continue
        desc = desc_map.get(ded.descuento_id)
        if not desc:
            continue
        nro = int(desc.nro_colegio or 0)
        if nro == NRO_COLEGIO_CONTRIB_HONORARIOS:
            base = hon_map.get(ded.medico_id, Decimal("0"))
        elif nro == NRO_COLEGIO_CONTRIB_GASTOS:
            base = gas_map.get(ded.medico_id, Decimal("0"))
        else:
            base = bruto_map.get(ded.medico_id, Decimal("0"))

        nuevo = (base * ded.porcentaje_aplicado / Decimal(100)).quantize(TWOPLACES)
        if nuevo != ded.calculado_total:
            ded.calculado_total = nuevo
            ids_recalculadas.append(ded.id)

    return {"cantidad": len(ids_recalculadas), "ids": ids_recalculadas}


async def _auto_generar_deducciones_porcentuales(db: AsyncSession, pago: Pago) -> dict:
    """
    Crea deducciones automáticas para descuentos con porcentaje > 0 que no tengan
    una deduccion activa en el período del pago.

    - aplica_a_todos=True : genera para todos los médicos con bruto > 0 en el pago
      (misma lógica que bulk_generar_descuento).
    - aplica_a_todos=False: genera solo para los médicos en socio_descuento.

    Se crean con calculado_total=0 como placeholder;
    recalcular_automaticas_porcentuales las actualiza con el bruto real.
    """
    desc_rows = (await db.execute(
        select(Descuentos).where(Descuentos.porcentaje > 0, Descuentos.nro_colegio != 200)
    )).scalars().all()

    if not desc_rows:
        return {"cantidad": 0, "ids": []}

    # Cargamos el bruto map una sola vez si hay algún desc aplica_a_todos
    bruto_map: dict[int, Decimal] | None = None
    if any(getattr(d, "aplica_a_todos", False) for d in desc_rows):
        bruto_map = await _base_bruto_por_medico_en_pago(db, pago.id)

    creados: list[Deduccion] = []

    for desc in desc_rows:
        if getattr(desc, "aplica_a_todos", False):
            # Todos los médicos con bruto > 0 en este pago, paga_por_caja=False
            med_tuples: list[tuple[int, bool]] = [
                (mid, False) for mid, bruto in (bruto_map or {}).items() if bruto > 0
            ]
        else:
            med_tuples = await _medicos_para_descuento(db, desc.id)

        if not med_tuples:
            continue

        med_ids = [m for m, _ in med_tuples]

        ya_existen: set[int] = set(
            (await db.execute(
                select(Deduccion.medico_id).where(
                    Deduccion.descuento_id == desc.id,
                    Deduccion.mes_aplicar == pago.mes,
                    Deduccion.anio_aplicar == pago.anio,
                    Deduccion.medico_id.in_(med_ids),
                    Deduccion.estado != "eliminado",
                )
            )).scalars().all()
        )

        for mid, paga_caja in med_tuples:
            if mid in ya_existen:
                continue
            ded = Deduccion(
                medico_id=mid,
                descuento_id=desc.id,
                calculado_total=Decimal("0.00"),
                porcentaje_aplicado=Decimal(str(desc.porcentaje or 0)),
                monto_aplicado=Decimal("0.00"),
                origen="automatico",
                paga_por_caja=paga_caja,
                estado="pendiente" if paga_caja else "en_pago",
                cuota_nro=0,
                mes_aplicar=pago.mes,
                anio_aplicar=pago.anio,
                generado_en_pago_id=None if paga_caja else pago.id,
            )
            db.add(ded)
            creados.append(ded)

    if creados:
        await db.flush()

    return {"cantidad": len(creados), "ids": [d.id for d in creados]}


async def _recalcular_montos_aplicados_en_pago(db: AsyncSession, pago_id: int) -> None:
    """
    Recalcula monto_aplicado de todas las deducciones en_pago del pago
    usando la lógica menor-a-mayor por pagador, sin cambiar estado ni
    crear registros DeduccionAplicacion. Es la función de 'preview':
    muestra cuánto se descontaría a cada médico con el disponible actual.
    Se resetea monto_aplicado=0 antes de recalcular para partir desde cero.
    """
    # Primero resetear monto_aplicado=0 para partir desde cero
    await db.execute(
        update(Deduccion)
        .where(
            Deduccion.estado == "en_pago",
            Deduccion.generado_en_pago_id == pago_id,
        )
        .values(monto_aplicado=Decimal("0.00"))
    )
    await db.flush()

    # populate_existing=True fuerza recargar desde DB, ignorando el identity map
    # (necesario cuando recalcular_automaticas_porcentuales acaba de actualizar
    # calculado_total via bulk UPDATE — los objetos en memoria pueden estar stale)
    deds = (await db.execute(
        select(Deduccion)
        .where(
            Deduccion.estado == "en_pago",
            Deduccion.generado_en_pago_id == pago_id,
        )
        .execution_options(populate_existing=True)
    )).scalars().all()

    if not deds:
        return

    disponible_map = await _disponible_por_medico_en_pago(db, pago_id)

    by_pagador: dict[int, list[Deduccion]] = defaultdict(list)
    for ded in deds:
        pagador = ded.pagador_medico_id if ded.pagador_medico_id is not None else ded.medico_id
        by_pagador[pagador].append(ded)

    # IDs de descuentos con prioridad fija (siempre se descuentan primero)
    DESCUENTOS_PRIORITARIOS: set[int] = {1, 2, 3}

    for pagador_id, pagador_deds in by_pagador.items():
        disponible = disponible_map.get(pagador_id, Decimal("0"))
        if disponible <= Decimal("0"):
            continue

        # Prioritarios primero (en orden de id), luego el resto menor-a-mayor
        prioritarios = sorted(
            [d for d in pagador_deds if d.descuento_id in DESCUENTOS_PRIORITARIOS],
            key=lambda d: d.descuento_id,
        )
        resto = sorted(
            [d for d in pagador_deds if d.descuento_id not in DESCUENTOS_PRIORITARIOS],
            key=lambda d: (d.calculado_total, d.id),
        )
        ordered_deds = prioritarios + resto

        restante = disponible

        for ded in ordered_deds:
            saldo = ded.calculado_total  # monto_aplicado fue reseteado a 0
            if saldo <= Decimal("0") or restante <= Decimal("0"):
                continue
            tomar = min(saldo, restante)
            restante -= tomar
            await db.execute(
                update(Deduccion)
                .where(Deduccion.id == ded.id)
                .values(monto_aplicado=tomar)
            )


async def generar_y_recalcular_porcentuales(db: AsyncSession, pago_id: int) -> dict:
    """
    Agrupa la auto-generación + recálculo de deducciones porcentuales automáticas.

    Debe llamarse cuando el bruto del pago cambia, específicamente:
      - Se crea o elimina una Liquidacion (factura)
      - Un LoteAjuste transiciona a estado 'L' (entra al pago) o vuelve a 'C' (sale del pago)

    No hace commit — el caller es responsable de hacerlo.
    No se llama al crear el pago (en ese momento no hay bruto aún).
    """
    pago = await db.get(Pago, pago_id)
    if not pago or pago.estado == "C":
        return {"generadas": 0, "recalculadas": 0}

    # Flush explícito para que los DetalleLiquidacion recién escritos
    # sean visibles al calcular el bruto en recalcular_automaticas_porcentuales.
    await db.flush()

    generadas = await _auto_generar_deducciones_porcentuales(db, pago)

    # Flush explícito para que las Deduccion recién creadas con calculado_total=0
    # sean visibles en la query de recalcular_automaticas_porcentuales.
    await db.flush()

    recalculadas = await recalcular_automaticas_porcentuales(db, pago_id)

    # Flush para que los calculado_total recién actualizados sean visibles
    # al recalcular el preview de monto_aplicado.
    await db.flush()

    await _recalcular_montos_aplicados_en_pago(db, pago_id)
    pago.deducciones_dirty = False

    return {
        "generadas": generadas["cantidad"],
        "generadas_ids": generadas["ids"],
        "recalculadas": recalculadas["cantidad"],
        "recalculadas_ids": recalculadas["ids"],
    }


async def refrescar_deducciones_pago(db: AsyncSession, pago: Pago) -> dict:
    """
    Refresco manual del pago abierto. Orquesta en orden:
    1. Auto-enrola deducciones manuales pendientes cuyo período <= pago.
    2. Recalcula monto_aplicado (preview menor-a-mayor) para todas las en_pago.
    3. Resetea deducciones_dirty = False.

    Nota: la generación + recálculo de porcentuales automáticas NO se ejecuta aquí;
    se dispara directamente al crear/eliminar una factura o al mover un lote a/desde
    liquidacion, vía generar_y_recalcular_porcentuales().
    """
    enroladas = await auto_enrolar_pendientes(db, pago)
    await _recalcular_montos_aplicados_en_pago(db, pago.id)
    pago.deducciones_dirty = False
    await db.commit()

    return {
        "pago_id": pago.id,
        "manuales_enroladas": enroladas["cantidad"],
        "manuales_enroladas_ids": enroladas["ids"],
    }


# =============================================================================
# Creación de deducciones manuales (cuotas)
# =============================================================================

async def crear_programa(db: AsyncSession, payload: DeduccionCreate) -> List[DeduccionRead]:
    n = len(payload.cuotas)
    monto_total = Decimal(str(payload.monto_total))
    cuota_base = _floor2(monto_total / Decimal(n))
    resto = monto_total - cuota_base * n

    open_pago = await _get_open_pago(db)

    cuotificado = n > 1
    mes, anio = payload.mes_inicio, payload.anio_inicio

    creados: list[Deduccion] = []

    for i in range(1, n + 1):
        cuota_cfg = payload.cuotas[i - 1]
        paga_caja = cuota_cfg.paga_por_caja

        monto_cuota = cuota_base + (resto if i == n else Decimal("0"))
        monto_cuota = monto_cuota.quantize(TWOPLACES)

        # Enrolar en pago abierto si el período aplica y la cuota no paga por caja
        if (
            not paga_caja
            and open_pago
            and (anio < open_pago.anio or (anio == open_pago.anio and mes <= open_pago.mes))
        ):
            estado = "en_pago"
        else:
            estado = "pendiente"

        ded = Deduccion(
            medico_id=payload.medico_id,
            descuento_id=payload.descuento_id,
            origen="manual",
            estado=estado,
            paga_por_caja=paga_caja,
            monto_total=monto_total,
            monto_cuota=monto_cuota,
            calculado_total=monto_cuota,
            monto_aplicado=Decimal("0.00"),
            cuotas_total=n,
            cuota_nro=i,
            cuotificado=cuotificado,
            mes_aplicar=mes,
            anio_aplicar=anio,
            pagador_medico_id=payload.pagador_medico_id,
            generado_en_pago_id=open_pago.id if estado == "en_pago" else None,
        )
        db.add(ded)
        creados.append(ded)

        if i < n:
            mes, anio = _advance_month(mes, anio)

    await db.flush()

    # Marcar dirty si hay pago abierto y se enroló alguna
    if open_pago and any(d.estado == "en_pago" for d in creados):
        await marcar_deducciones_dirty(db, open_pago.id)

    await db.commit()
    for d in creados:
        await db.refresh(d)

    return await enrich_many(db, creados)


# =============================================================================
# Historial unificado
# =============================================================================

async def fetch_deducciones_item(
    db: AsyncSession,
    medico_id: Optional[int] = None,
    descuento_id: Optional[int] = None,
    estado: Optional[str] = None,
    origen: Optional[str] = None,
    paga_por_caja: Optional[bool] = None,
    mes_desde: Optional[int] = None,
    anio_desde: Optional[int] = None,
    mes_hasta: Optional[int] = None,
    anio_hasta: Optional[int] = None,
) -> list[DeduccionHistorialItem]:
    stmt = (
        select(
            Deduccion,
            Descuentos.nombre.label("desc_nombre"),
            ListadoMedico.NOMBRE.label("med_nombre"),
        )
        .outerjoin(Descuentos, Descuentos.id == Deduccion.descuento_id)
        .join(ListadoMedico, ListadoMedico.ID == Deduccion.medico_id)
        .where(Deduccion.estado != "eliminado")
    )

    if medico_id is not None:
        stmt = stmt.where(Deduccion.medico_id == medico_id)
    if descuento_id is not None:
        stmt = stmt.where(Deduccion.descuento_id == descuento_id)
    if origen:
        stmt = stmt.where(Deduccion.origen == origen)
    if paga_por_caja is not None:
        stmt = stmt.where(Deduccion.paga_por_caja == paga_por_caja)

    rows = (await db.execute(stmt)).all()

    periodo_desde = anio_desde * 12 + mes_desde if anio_desde and mes_desde else None
    periodo_hasta = anio_hasta * 12 + mes_hasta if anio_hasta and mes_hasta else None

    items: list[DeduccionHistorialItem] = []

    for row in rows:
        ded: Deduccion = row[0]
        desc_nombre: str = row[1] or ""
        med_nombre: str = row[2] or ""
        paga_caja: bool = bool(ded.paga_por_caja)

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

        mes_periodo = ded.mes_aplicar
        anio_periodo = ded.anio_aplicar

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
            paga_por_caja=paga_caja,
            medico_id=ded.medico_id,
            medico_nombre=med_nombre,
            descuento_id=ded.descuento_id,
            descuento_nombre=desc_nombre,
            monto=ded.calculado_total,
            saldo_pendiente=ded.calculado_total - ded.monto_aplicado,
            mes_periodo=mes_periodo,
            anio_periodo=anio_periodo,
            estado=est,
            cuota_nro=ded.cuota_nro,
            cuotas_total=ded.cuotas_total,
            generado_en_pago_id=ded.generado_en_pago_id,
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
    paga_por_caja: Optional[bool] = None,
    mes_desde: Optional[int] = None,
    anio_desde: Optional[int] = None,
    mes_hasta: Optional[int] = None,
    anio_hasta: Optional[int] = None,
) -> DeduccionHistorialPage:
    items = await fetch_deducciones_item(
        db,
        medico_id=medico_id,
        descuento_id=descuento_id,
        estado=estado,
        origen=origen,
        paga_por_caja=paga_por_caja,
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
    paga_por_caja: Optional[bool] = None,
    mes_desde: Optional[int] = None,
    anio_desde: Optional[int] = None,
    mes_hasta: Optional[int] = None,
    anio_hasta: Optional[int] = None,
) -> list[DeduccionHistorialItem]:
    return await fetch_deducciones_item(
        db,
        medico_id=medico_id,
        descuento_id=descuento_id,
        estado=estado,
        origen=origen,
        paga_por_caja=paga_por_caja,
        mes_desde=mes_desde,
        anio_desde=anio_desde,
        mes_hasta=mes_hasta,
        anio_hasta=anio_hasta,
    )


async def get_deducciones_aplicadas(
    db: AsyncSession,
    pago_id: int,
) -> DeduccionesAplicadasResponse:
    """
    Devuelve las deducciones que se están aplicando en un pago.

    - Pago abierto ('A'): lee desde `Deduccion` donde estado='en_pago'
      y monto_aplicado != 0, con generado_en_pago_id = pago_id.
    - Pago cerrado ('C'): lee desde `DeduccionAplicacion` donde pago_id = pago_id,
      tomando directamente el campo `aplicado`.
    """
    from app.modules.deducciones.schemas import DeduccionAplicadaItem

    pago = await db.get(Pago, pago_id)
    if not pago:
        from fastapi import HTTPException
        raise HTTPException(404, "Pago no encontrado")

    items: list[DeduccionAplicadaItem] = []

    if pago.estado != "C":
        # Pago abierto — fuente: tabla deducciones
        rows = (await db.execute(
            select(
                Deduccion.medico_id,
                Deduccion.descuento_id,
                Deduccion.monto_aplicado,
                Descuentos.nombre.label("desc_nombre"),
                ListadoMedico.NOMBRE.label("med_nombre"),
                ListadoMedico.NRO_SOCIO.label("nro_socio"),
            )
            .outerjoin(Descuentos, Descuentos.id == Deduccion.descuento_id)
            .join(ListadoMedico, ListadoMedico.ID == Deduccion.medico_id)
            .where(
                Deduccion.generado_en_pago_id == pago_id,
                Deduccion.estado == "en_pago",
                Deduccion.monto_aplicado != 0,
            )
            .order_by(ListadoMedico.NOMBRE)
        )).mappings().all()

        for r in rows:
            items.append(DeduccionAplicadaItem(
                medico_id=int(r["medico_id"]),
                medico_nombre=(r["med_nombre"] or "").strip(),
                medico_nro_socio=int(r["nro_socio"]),
                descuento_id=r["descuento_id"],
                descuento_nombre=r["desc_nombre"] or "",
                monto_aplicado=Decimal(str(r["monto_aplicado"] or "0")),
            ))
    else:
        # Pago cerrado — fuente: tabla deduccion_aplicacion
        rows = (await db.execute(
            select(
                DeduccionAplicacion.aplicado,
                Deduccion.medico_id,
                Deduccion.descuento_id,
                Descuentos.nombre.label("desc_nombre"),
                ListadoMedico.NOMBRE.label("med_nombre"),
                ListadoMedico.NRO_SOCIO.label("nro_socio"),
            )
            .join(Deduccion, Deduccion.id == DeduccionAplicacion.deduccion_id)
            .outerjoin(Descuentos, Descuentos.id == Deduccion.descuento_id)
            .join(ListadoMedico, ListadoMedico.ID == Deduccion.medico_id)
            .where(DeduccionAplicacion.pago_id == pago_id)
            .order_by(ListadoMedico.NOMBRE)
        )).mappings().all()

        for r in rows:
            items.append(DeduccionAplicadaItem(
                medico_id=int(r["medico_id"]),
                medico_nombre=(r["med_nombre"] or "").strip(),
                medico_nro_socio=int(r["nro_socio"]),
                descuento_id=r["descuento_id"],
                descuento_nombre=r["desc_nombre"] or "",
                monto_aplicado=Decimal(str(r["aplicado"] or "0")),
            ))

    total_aplicado = sum((i.monto_aplicado for i in items), Decimal("0.00"))

    return DeduccionesAplicadasResponse(
        pago_id=pago_id,
        pago_estado=pago.estado,
        total_items=len(items),
        total_aplicado=total_aplicado,
        items=items,
    )


async def get_top_deudores(db: AsyncSession, limit: int = 10) -> list[TopDeudorItem]:
    """Top médicos con mayor saldo pendiente (calculado_total - monto_aplicado)."""
    rows = (
        await db.execute(
            select(
                Deduccion.medico_id,
                func.sum(Deduccion.calculado_total - Deduccion.monto_aplicado).label("saldo_total"),
                ListadoMedico.NOMBRE.label("med_nombre"),
                ListadoMedico.NRO_SOCIO.label("nro_socio"),
            )
            .join(ListadoMedico, ListadoMedico.ID == Deduccion.medico_id)
            .where(Deduccion.estado.in_(["pendiente", "en_pago"]))
            .group_by(Deduccion.medico_id, ListadoMedico.NOMBRE, ListadoMedico.NRO_SOCIO)
            .having(func.sum(Deduccion.calculado_total - Deduccion.monto_aplicado) > 0)
            .order_by(func.sum(Deduccion.calculado_total - Deduccion.monto_aplicado).desc())
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


# Máquina de estados unificada para todas las deducciones.
TRANSICIONES_VALIDAS = {
    ("pendiente", "aplicado"),   # cobro directo en caja (paga_por_caja=True o manual)
    ("aplicado", "pendiente"),   # revertir cobro en caja
    ("pendiente", "en_pago"),    # enrolar en pago abierto
    ("en_pago", "pendiente"),    # quitar del pago
    ("en_pago", "aplicado"),     # marcar como aplicado manualmente
    ("pendiente", "cancelado"),
    ("en_pago", "cancelado"),
    ("aplicado", "cancelado"),
}


async def cambiar_estado_item(db: AsyncSession, id: int, nuevo_estado: str) -> DeduccionHistorialItem:
    ded = await db.get(Deduccion, id)
    if not ded:
        raise ValueError("not_found")

    actual = ded.estado
    if actual == nuevo_estado:
        raise ValueError("mismo_estado")
    if actual in ("cancelado", "eliminado"):
        raise ValueError(f"no_editable:{actual}")

    db_actual = "pendiente" if actual == "vencida" else actual

    if (db_actual, nuevo_estado) not in TRANSICIONES_VALIDAS:
        raise ValueError(f"transicion_invalida:{db_actual}:{nuevo_estado}")

    # Side effects por transición
    if nuevo_estado == "aplicado":
        # Crear o actualizar DeduccionAplicacion sin pago_id (cobro en caja / manual).
        # ON DUPLICATE KEY no funciona para NULL en MySQL — verificamos explícitamente.
        existing_apl = await db.scalar(
            select(DeduccionAplicacion.id).where(
                DeduccionAplicacion.deduccion_id == ded.id,
                DeduccionAplicacion.pago_id.is_(None),
            )
        )
        if existing_apl:
            await db.execute(
                update(DeduccionAplicacion)
                .where(DeduccionAplicacion.id == existing_apl)
                .values(aplicado=ded.calculado_total)
            )
        else:
            db.add(DeduccionAplicacion(
                pago_id=None,
                deduccion_id=ded.id,
                aplicado=ded.calculado_total,
            ))
        ded.monto_aplicado = ded.calculado_total
        if db_actual == "en_pago":
            ded.generado_en_pago_id = None
            pago = await _get_open_pago(db)
            if pago:
                await marcar_deducciones_dirty(db, pago.id)

    elif db_actual == "aplicado" and nuevo_estado == "pendiente":
        # Revertir cobro en caja
        await db.execute(
            delete(DeduccionAplicacion).where(
                DeduccionAplicacion.deduccion_id == ded.id,
                DeduccionAplicacion.pago_id.is_(None),
            )
        )
        ded.monto_aplicado = Decimal("0.00")

    elif nuevo_estado == "en_pago":
        pago = await _get_open_pago(db)
        if not pago:
            raise ValueError("no_pago_abierto")
        ded.generado_en_pago_id = pago.id
        await marcar_deducciones_dirty(db, pago.id)

    elif db_actual == "en_pago" and nuevo_estado in ("pendiente", "cancelado"):
        ded.generado_en_pago_id = None
        pago = await _get_open_pago(db)
        if pago:
            await marcar_deducciones_dirty(db, pago.id)

    ded.estado = nuevo_estado
    await db.commit()
    await db.refresh(ded)
    return await _ded_to_historial(db, ded)


async def pagar_deduccion(db: AsyncSession, id: int) -> DeduccionHistorialItem:
    """
    Endpoint 'Pagar': marca la deducción como pagada en caja independientemente
    del pago abierto. Fuerza paga_por_caja=True y origen='manual', crea
    DeduccionAplicacion(pago_id=None).
    """
    ded = await db.get(Deduccion, id)
    if not ded:
        raise ValueError("not_found")
    if ded.estado in ("aplicado", "cancelado", "eliminado"):
        raise ValueError(f"no_editable:{ded.estado}")

    # Si estaba en_pago, sacarlo del pago antes de aplicar en caja
    if ded.estado == "en_pago":
        ded.generado_en_pago_id = None
        pago = await _get_open_pago(db)
        if pago:
            await marcar_deducciones_dirty(db, pago.id)

    ded.paga_por_caja = True
    ded.origen = "manual"
    ded.estado = "aplicado"
    ded.monto_aplicado = ded.calculado_total

    existing_apl = await db.scalar(
        select(DeduccionAplicacion.id).where(
            DeduccionAplicacion.deduccion_id == ded.id,
            DeduccionAplicacion.pago_id.is_(None),
        )
    )
    if existing_apl:
        await db.execute(
            update(DeduccionAplicacion)
            .where(DeduccionAplicacion.id == existing_apl)
            .values(aplicado=ded.calculado_total)
        )
    else:
        db.add(DeduccionAplicacion(
            pago_id=None,
            deduccion_id=ded.id,
            aplicado=ded.calculado_total,
        ))

    await db.commit()
    await db.refresh(ded)
    return await _ded_to_historial(db, ded)


async def cambiar_monto_item(db: AsyncSession, id: int, monto: Decimal) -> DeduccionHistorialItem:
    ded = await db.get(Deduccion, id)
    if not ded:
        raise ValueError("not_found")
    if ded.estado in ("aplicado", "cancelado"):
        raise ValueError(f"no_editable:{ded.estado}")

    monto = monto.quantize(TWOPLACES)
    ded.calculado_total = monto
    if ded.origen == "manual":
        ded.monto_cuota = monto

    if ded.estado == "en_pago":
        pago = await _get_open_pago(db)
        if pago:
            await marcar_deducciones_dirty(db, pago.id)

    await db.commit()
    await db.refresh(ded)
    return await _ded_to_historial(db, ded)


async def eliminar_item(db: AsyncSession, id: int) -> DeduccionItemEliminarResponse:
    ded = await db.get(Deduccion, id)
    if not ded:
        raise ValueError("not_found")
    if ded.estado == "aplicado":
        # Aplicado solo se puede eliminar si el pago sigue abierto
        pago = await _get_open_pago(db)
        if not pago:
            raise ValueError("no_editable:aplicado")

    if ded.estado == "en_pago":
        pago = await _get_open_pago(db)
        if pago:
            await marcar_deducciones_dirty(db, pago.id)

    if ded.origen == "manual":
        ded.estado = "eliminado"
        await db.commit()
        return DeduccionItemEliminarResponse(id=id, origen="manual", estado="eliminado")
    else:
        await db.delete(ded)
        await db.commit()
        return DeduccionItemEliminarResponse(id=id, origen="automatico", estado="eliminado")


# =============================================================================
# Aplicar deducciones al cierre del pago
# =============================================================================

async def _persistir_aplicaciones(
    db: AsyncSession,
    pago_id: int,
    apl_values: list[dict],
    monto_aplicado_delta: list[tuple[int, Decimal]],
    ids_aplicados: list[int],
    ids_pendientes: list[int],
) -> int:
    """
    Escribe a DB los resultados del algoritmo de distribución:
    - Inserta/actualiza DeduccionAplicacion (ON DUPLICATE KEY UPDATE).
    - Suma los deltas de monto_aplicado en Deduccion.
    - Marca completamente cubiertas → 'aplicado'.
    - Marca parciales o sin cobertura → 'pendiente'.
    Devuelve la cantidad de deducciones parciales.
    """
    if apl_values:
        apl_tbl = DeduccionAplicacion.__table__
        stmt_apl = mysql_insert(apl_tbl).values(apl_values)
        await db.execute(
            stmt_apl.on_duplicate_key_update(
                aplicado=apl_tbl.c.aplicado + stmt_apl.inserted.aplicado
            )
        )

    for ded_id, delta in monto_aplicado_delta:
        await db.execute(
            update(Deduccion)
            .where(Deduccion.id == ded_id)
            .values(monto_aplicado=Deduccion.monto_aplicado + delta)
        )

    if ids_aplicados:
        await db.execute(
            update(Deduccion)
            .where(Deduccion.id.in_(ids_aplicados))
            .values(estado="aplicado")
        )

    if ids_pendientes:
        await db.execute(
            update(Deduccion)
            .where(Deduccion.id.in_(ids_pendientes))
            .values(estado="pendiente")
        )

    await db.commit()

    pendientes_set = set(ids_pendientes)
    return len([ded_id for ded_id, _ in monto_aplicado_delta if ded_id in pendientes_set])


async def aplicar_deducciones_al_cierre(db: AsyncSession, pago_id: int) -> dict:
    """
    Se llama al cerrar el pago. Procesa todas las Deduccion en estado 'en_pago'.

    Por cada pagador efectivo (medico o pagador_medico_id):
    - Calcula disponible = bruto - débitos + créditos
    - Ordena deducciones por saldo_pendiente ASC (menor a mayor)
    - Aplica las que entren completas; el resto recibe lo que queda (parcial)
    - Escribe DeduccionAplicacion, actualiza monto_aplicado
    - Completas → estado='aplicado'; parciales o sin cobertura → estado='pendiente'
    """
    deds = (
        await db.execute(
            select(Deduccion).where(Deduccion.estado == "en_pago")
        )
    ).scalars().all()

    if not deds:
        return {"pago_id": pago_id, "aplicadas": 0, "pendientes": 0, "parciales": 0, "total_aplicado": "0.00"}

    disponible_map = await _disponible_por_medico_en_pago(db, pago_id)

    # Agrupar por pagador efectivo
    by_pagador: dict[int, list[Deduccion]] = defaultdict(list)
    for ded in deds:
        pagador = ded.pagador_medico_id if ded.pagador_medico_id is not None else ded.medico_id
        by_pagador[pagador].append(ded)

    ids_aplicados: list[int] = []
    ids_pendientes: list[int] = []
    apl_values: list[dict] = []
    monto_aplicado_delta: list[tuple[int, Decimal]] = []  # (ded.id, delta)
    total_aplicado = Decimal("0.00")

    for pagador_id, pagador_deds in by_pagador.items():
        disponible = disponible_map.get(pagador_id, Decimal("0"))
        if disponible <= Decimal("0"):
            ids_pendientes.extend(d.id for d in pagador_deds)
            continue

        # Ordenar por saldo pendiente ASC (menor a mayor), empate por id ASC
        sorted_deds = sorted(
            pagador_deds,
            key=lambda d: (d.calculado_total - d.monto_aplicado, d.id),
        )

        restante = disponible
        for ded in sorted_deds:
            saldo = ded.calculado_total - ded.monto_aplicado
            if saldo <= Decimal("0"):
                ids_aplicados.append(ded.id)
                continue

            if restante <= Decimal("0"):
                ids_pendientes.append(ded.id)
                continue

            tomar = min(saldo, restante)
            restante -= tomar
            total_aplicado += tomar
            monto_aplicado_delta.append((ded.id, tomar))

            apl_values.append({
                "pago_id": pago_id,
                "deduccion_id": ded.id,
                "aplicado": tomar,
            })

            if tomar >= saldo:
                ids_aplicados.append(ded.id)
            else:
                ids_pendientes.append(ded.id)

    parciales = await _persistir_aplicaciones(
        db, pago_id, apl_values, monto_aplicado_delta, ids_aplicados, ids_pendientes
    )
    return {
        "pago_id": pago_id,
        "aplicadas": len(ids_aplicados),
        "pendientes": len(ids_pendientes),
        "parciales": parciales,
        "total_aplicado": str(total_aplicado.quantize(TWOPLACES)),
    }


# =============================================================================
# Verificar y deshacer descuentos por pago
# =============================================================================

async def verificar_deducciones_por_pago(db: AsyncSession, pago_id: int) -> DeduccionPorPagoResponse:
    deds = (
        await db.execute(
            select(Deduccion)
            .where(
                Deduccion.estado == "en_pago",
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


async def deshacer_descuentos_generados(
    db: AsyncSession,
    pago_id: int,
    desc_id: Optional[int] = None,
) -> DeshacerDescuentosResponse:
    """
    Deshace los efectos de bulk_generar_descuento para este pago:

    1. Elimina físicamente las deducciones creadas por el pago
       (generado_en_pago_id = pago_id).

    2. Revierte a 'pendiente' las deducciones que estaban en_pago pero NO fueron
       creadas por este pago (generado_en_pago_id IS NULL) — son las enroladas.

    Si se pasa desc_id, ambas operaciones se restringen a ese descuento.
    """
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise ValueError("pago_no_encontrado")
    if pago.estado == "C":
        raise ValueError("pago_cerrado")

    # ── 1. Eliminar las generadas por este pago ──────────────────────────────
    filtro_generadas = [Deduccion.generado_en_pago_id == pago_id]
    if desc_id is not None:
        filtro_generadas.append(Deduccion.descuento_id == desc_id)

    generadas = (
        await db.execute(select(Deduccion).where(*filtro_generadas))
    ).scalars().all()

    monto_revertido = sum((Decimal(str(d.calculado_total or 0)) for d in generadas), Decimal("0.00"))
    ids_eliminar = [d.id for d in generadas]

    if ids_eliminar:
        await db.execute(delete(Deduccion).where(Deduccion.id.in_(ids_eliminar)))

    # ── 2. Revertir a pendiente las enroladas (en_pago sin generado_pago_id) ─
    filtro_enroladas = [
        Deduccion.estado == "en_pago",
        Deduccion.generado_en_pago_id.is_(None),
    ]
    if desc_id is not None:
        filtro_enroladas.append(Deduccion.descuento_id == desc_id)

    await db.execute(
        update(Deduccion)
        .where(*filtro_enroladas)
        .values(estado="pendiente")
    )

    pago.deducciones_dirty = False
    await db.commit()

    return DeshacerDescuentosResponse(
        pago_id=pago_id,
        eliminadas=len(ids_eliminar),
        monto_revertido=monto_revertido.quantize(TWOPLACES),
    )

