import datetime
from collections import defaultdict
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, case, func, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
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
from app.db.models.financiero import Descuentos as _Descuentos  # alias to avoid shadowing
from app.modules.deducciones.schemas import (
    DeduccionCreate,
    DeduccionEstadoPayload,
    DeduccionGrupoUpdate,
    DeduccionHistorialItem,
    DeduccionHistorialPage,
    DeduccionItemEliminarResponse,
    DeduccionItemMontoPayload,
    DeduccionPorPagoResponse,
    DeduccionRead,
    DeshacerDescuentosResponse,
    SocioDescuentoCreate,
    SocioDescuentoPagadorUpdate,
    SocioDescuentoRead,
    SocioDescuentoUpdate,
    TopDeudorItem,
)
from app.modules.deducciones.service import (
    auto_asignar_programas,
    cambiar_estado_item,
    cambiar_monto_item,
    crear_programa,
    deshacer_descuentos_generados,
    eliminar_item,
    enrich_many,
    generar_programas_en_pago,
    get_historial_deducciones,
    get_historial_export,
    get_top_deudores,
    verificar_deducciones_por_pago,
    _enrich_programa,
)

router = APIRouter()
TWOPLACES = Decimal("0.01")


# nro_colegio de descuentos con base de cálculo especial
NRO_COLEGIO_CONTRIB_HONORARIOS = 100  # base = suma de honorarios del médico
NRO_COLEGIO_CONTRIB_GASTOS = 101      # base = suma de gastos del médico


# region Helpers
async def _base_bruto_por_medico_en_pago(
    db: AsyncSession, pago_id: int
) -> dict[int, Decimal]:
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
    out = {}
    for med_id, suma in q:
        out[int(med_id)] = Decimal(suma or 0)
    return out


async def _honorarios_por_medico_en_pago(
    db: AsyncSession, pago_id: int
) -> dict[int, Decimal]:
    """Suma de DetalleLiquidacion.honorarios por médico (listado_medico.ID)."""
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
    """Suma de DetalleLiquidacion.gastos por médico (listado_medico.ID)."""
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


async def _get_descuento(db: AsyncSession, desc_id: int) -> Descuentos:
    obj = await db.scalar(select(Descuentos).where(Descuentos.id == desc_id))
    if not obj:
        raise ValueError("Descuento inexistente")
    return obj


async def _medicos_para_descuento(db: AsyncSession, desc_id: int) -> List[int]:
    res = await db.execute(
        select(SocioDescuento.medico_id).where(SocioDescuento.descuento_id == desc_id)
    )
    return list({m for (m,) in res.all()})


def _calc_monto(
    p_base: Decimal, precio: Decimal | None, porcentaje: Decimal | None
) -> tuple[Decimal, Decimal | None]:
    if porcentaje and porcentaje > 0:
        m = (p_base * porcentaje / Decimal(100)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
        return (m, porcentaje)
    if precio and precio > 0:
        return (precio.quantize(TWOPLACES, rounding=ROUND_HALF_UP), None)
    return (Decimal(0), None)


async def _disponible_por_medico_en_pago(
    db: AsyncSession, pago_id: int
) -> dict[int, Decimal]:
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
    deb_map, cred_map = {}, {}
    for med, deb, cred in qaj:
        deb_map[int(med)] = Decimal(deb or 0)
        cred_map[int(med)] = Decimal(cred or 0)

    out: dict[int, Decimal] = {}
    keys = set(bruto_map) | set(deb_map) | set(cred_map)
    for k in keys:
        out[k] = (
            bruto_map.get(k, Decimal("0"))
            - deb_map.get(k, Decimal("0"))
            + cred_map.get(k, Decimal("0"))
        )
    return out


# endregion


@router.post("/{pago_id}/colegio/bulk_generar_descuento/{desc_id}")
async def bulk_generar_descuento(
    pago_id: int, desc_id: int, db: AsyncSession = Depends(get_db)
):
    """
    Calcula y registra el monto a descontar para cada médico asignado al descuento.
    Crea/actualiza Deduccion (origen='automatico') y acumula en DeduccionSaldo.
    """
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")
    if pago.estado == "C":
        raise HTTPException(409, "El pago está cerrado")

    try:
        desc = await _get_descuento(db, desc_id)
    except ValueError:
        raise HTTPException(404, "Descuento no encontrado")

    nro_colegio = int(desc.nro_colegio or 0)
    if nro_colegio == NRO_COLEGIO_CONTRIB_HONORARIOS:
        base_por_med = await _honorarios_por_medico_en_pago(db, pago_id)
    elif nro_colegio == NRO_COLEGIO_CONTRIB_GASTOS:
        base_por_med = await _gastos_por_medico_en_pago(db, pago_id)
    else:
        base_por_med = await _base_bruto_por_medico_en_pago(db, pago_id)

    if getattr(desc, "aplica_a_todos", False):
        med_ids = [mid for mid, bruto in base_por_med.items() if bruto > 0]
    else:
        med_ids = await _medicos_para_descuento(db, desc_id)

    if not med_ids:
        return {"generados": 0, "actualizados": 0, "cargado_total": 0}

    to_snapshot = []
    to_saldo = []

    precio = Decimal(str(getattr(desc, "precio", 0) or 0))
    porcentaje = Decimal(str(getattr(desc, "porcentaje", 0) or 0))
    total_cargado = Decimal(0)

    for mid in med_ids:
        base = base_por_med.get(mid, Decimal(0))
        monto, porc_usado = _calc_monto(base, precio, porcentaje)
        if monto <= 0:
            continue

        total_cargado += monto
        porc_ap = (porc_usado if porc_usado else Decimal("0")).quantize(
            TWOPLACES, rounding=ROUND_HALF_UP
        )

        to_snapshot.append(
            {
                "pago_id": pago_id,
                "medico_id": mid,
                "descuento_id": desc_id,
                "calculado_total": monto,
                "porcentaje_aplicado": porc_ap,
                "monto_aplicado": Decimal("0.00"),
                "origen": "automatico",
                "estado": "en_pago",
                "cuota_nro": 0,  # 0 = auto row (used in unique constraint)
                "mes_aplicar": pago.mes,
                "anio_aplicar": pago.anio,
            }
        )
        to_saldo.append(
            {
                "concepto_tipo": "desc",
                "concepto_id": desc_id,
                "medico_id": mid,
                "saldo": monto,
            }
        )

    if not to_snapshot:
        return {"generados": 0, "actualizados": 0, "cargado_total": 0}

    # Verificar que ninguno de los médicos ya tenga este descuento en este período
    med_ids_a_insertar = [r["medico_id"] for r in to_snapshot]
    existentes = (await db.execute(
        select(Deduccion.medico_id)
        .where(
            Deduccion.descuento_id == desc_id,
            Deduccion.mes_aplicar == pago.mes,
            Deduccion.anio_aplicar == pago.anio,
            Deduccion.medico_id.in_(med_ids_a_insertar),
        )
        .limit(1)
    )).first()
    if existentes:
        raise HTTPException(
            409,
            detail={
                "reason": "descuento_ya_generado",
                "message": f"El descuento {desc_id} ya fue generado para el período {pago.mes}/{pago.anio}. Elimine los registros existentes antes de regenerar.",
            },
        )

    snap_tbl = Deduccion.__table__
    stmt_snap = mysql_insert(snap_tbl).values(to_snapshot)
    await db.execute(stmt_snap)

    saldo_tbl = DeduccionSaldo.__table__
    stmt_saldo = mysql_insert(saldo_tbl).values(to_saldo)
    up_saldo = stmt_saldo.on_duplicate_key_update(
        saldo=saldo_tbl.c.saldo + stmt_saldo.inserted.saldo,
    )
    await db.execute(up_saldo)

    # Los rows quedan en estado='en_pago'. La aplicación real ocurre al cerrar el pago.
    await db.commit()

    return {
        "generados": len(to_snapshot),
        "actualizados": 0,
        "cargado_total": float(total_cargado.quantize(TWOPLACES)),
    }


@router.post("/{pago_id}/colegio/aplicar", status_code=status.HTTP_200_OK)
async def aplicar_deducciones_pago(
    pago_id: int,
    desc_id: int | None = Query(None, description="Opcional: aplicar sólo este descuento"),
    solo_generado_mes: bool = Query(True, description="True => sólo lo generado en este pago"),
    db: AsyncSession = Depends(get_db),
):
    """
    Aplica saldos de deducciones al disponible por médico en el pago.
    Lo que no entra queda en DeduccionSaldo para períodos futuros.
    """
    async with db.begin():
        pago = await db.get(Pago, pago_id)
        if not pago:
            raise HTTPException(404, "Pago no encontrado")
        if pago.estado == "C":
            raise HTTPException(409, "El pago está cerrado")

        disponible_por_med = await _disponible_por_medico_en_pago(db, pago_id)

        base = select(
            DeduccionSaldo.id,
            DeduccionSaldo.medico_id,
            DeduccionSaldo.concepto_id,
            DeduccionSaldo.concepto_tipo,
            DeduccionSaldo.saldo,
        ).where(DeduccionSaldo.concepto_tipo == "desc", DeduccionSaldo.saldo > 0)

        if solo_generado_mes:
            base = base.join(
                Deduccion,
                and_(
                    Deduccion.medico_id == DeduccionSaldo.medico_id,
                    Deduccion.pago_id == pago_id,
                    Deduccion.descuento_id == DeduccionSaldo.concepto_id,
                ),
            )

        if desc_id is not None:
            base = base.where(DeduccionSaldo.concepto_id == desc_id)

        rows = (
            await db.execute(base.order_by(DeduccionSaldo.medico_id, DeduccionSaldo.id))
        ).all()
        if not rows:
            return {
                "pago_id": pago_id,
                "medicos_afectados": 0,
                "aplicado_total": 0.0,
                "nota": "No hay saldos para aplicar bajo los criterios actuales.",
            }

        aplicado_por_med_desc: dict[tuple[int, int, str], Decimal] = defaultdict(
            lambda: Decimal("0.00")
        )
        updates_saldo: list[tuple[int, Decimal]] = []
        aplicados_total = Decimal("0.00")
        medicos_afectados: set[int] = set()

        current_med: int | None = None
        restante = Decimal("0.00")

        for saldo_id, med_id, concepto_id, concepto_tipo, saldo_val in rows:
            med_id = int(med_id)
            concepto_id = int(concepto_id)

            if med_id != current_med:
                current_med = med_id
                restante = Decimal(str(disponible_por_med.get(med_id, 0) or 0))
                if restante <= 0:
                    continue

            if restante <= 0:
                continue

            saldo_dec = Decimal(str(saldo_val or 0))
            if saldo_dec <= 0:
                continue

            tomar = saldo_dec if saldo_dec <= restante else restante
            if tomar <= 0:
                continue

            updates_saldo.append((int(saldo_id), tomar))
            aplicado_por_med_desc[(med_id, concepto_id, concepto_tipo)] += tomar
            aplicados_total += tomar
            medicos_afectados.add(med_id)
            restante -= tomar

        if not updates_saldo:
            return {
                "pago_id": pago_id,
                "medicos_afectados": 0,
                "aplicado_total": 0.0,
                "nota": "No había disponible en el período para aplicar más débitos.",
            }

        # Insertar en DeduccionAplicacion
        apl_tbl = DeduccionAplicacion.__table__
        apl_values = [
            {
                "pago_id": pago_id,
                "medico_id": med_id,
                "concepto_tipo": conc_tipo,
                "concepto_id": conc_id,
                "aplicado": monto,
            }
            for (med_id, conc_id, conc_tipo), monto in aplicado_por_med_desc.items()
        ]
        stmt_apl = mysql_insert(apl_tbl).values(apl_values)
        up_apl = stmt_apl.on_duplicate_key_update(
            aplicado=apl_tbl.c.aplicado + stmt_apl.inserted.aplicado
        )
        await db.execute(up_apl)

        for saldo_id, aplicado in updates_saldo:
            await db.execute(
                update(DeduccionSaldo)
                .where(DeduccionSaldo.id == saldo_id)
                .values(saldo=DeduccionSaldo.saldo - aplicado)
            )

        # Mark processed Deduccion rows as aplicado
        await db.execute(
            update(Deduccion)
            .where(
                Deduccion.pago_id == pago_id,
                Deduccion.estado == "en_pago",
                Deduccion.descuento_id.in_(
                    [conc_id for (_, conc_id, ct) in aplicado_por_med_desc if ct == "desc"]
                ),
            )
            .values(estado="aplicado")
        )

    return {
        "pago_id": pago_id,
        "medicos_afectados": len(medicos_afectados),
        "aplicado_total": float(aplicados_total),
        "nota": "Aplicado respetando el disponible por médico. Remanente queda en saldos.",
    }


# =============================================================================
# DEDUCCIONES MANUALES — Cuotas (origen='manual')
# =============================================================================

# region Programas CRUD

@router.post("/programas", response_model=List[DeduccionRead], status_code=201)
async def crear_programa_endpoint(
    payload: DeduccionCreate,
    db: AsyncSession = Depends(get_db),
):
    """Crea una deducción manual con 1 o N cuotas."""
    if not await db.scalar(select(ListadoMedico.ID).where(ListadoMedico.ID == payload.medico_id)):
        raise HTTPException(404, "Médico no encontrado")
    if not await db.scalar(select(Descuentos.id).where(Descuentos.id == payload.descuento_id)):
        raise HTTPException(404, "Descuento no encontrado")
    if payload.pagador_medico_id is not None:
        if not await db.scalar(select(ListadoMedico.ID).where(ListadoMedico.ID == payload.pagador_medico_id)):
            raise HTTPException(404, "Médico pagador no encontrado")

    return await crear_programa(db, payload)


@router.get("/programas/grupo/{grupo_id}", response_model=List[DeduccionRead])
async def listar_grupo(grupo_id: int, db: AsyncSession = Depends(get_db)):
    """Devuelve todas las cuotas de un grupo ordenadas por cuota_nro."""
    deds = (
        await db.execute(
            select(Deduccion)
            .where(Deduccion.grupo_id == grupo_id, Deduccion.origen == "manual")
            .order_by(Deduccion.cuota_nro)
        )
    ).scalars().all()
    if not deds:
        raise HTTPException(404, "Grupo no encontrado")
    return await enrich_many(db, list(deds))


@router.get("/programas", response_model=List[DeduccionRead])
async def listar_programas(
    medico_id: Optional[int] = Query(None),
    descuento_id: Optional[int] = Query(None),
    mes: Optional[int] = Query(None, ge=1, le=12),
    anio: Optional[int] = Query(None),
    estado: Optional[str] = Query(None),
    pago_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Deduccion)
        .where(Deduccion.origen == "manual")
        .order_by(Deduccion.anio_aplicar, Deduccion.mes_aplicar, Deduccion.id)
    )
    if medico_id is not None:
        stmt = stmt.where(Deduccion.medico_id == medico_id)
    if descuento_id is not None:
        stmt = stmt.where(Deduccion.descuento_id == descuento_id)
    if mes is not None:
        stmt = stmt.where(Deduccion.mes_aplicar == mes)
    if anio is not None:
        stmt = stmt.where(Deduccion.anio_aplicar == anio)
    if estado is not None:
        stmt = stmt.where(Deduccion.estado == estado)
    if pago_id is not None:
        stmt = stmt.where(Deduccion.pago_id == pago_id)

    deds = (await db.execute(stmt)).scalars().all()
    return await enrich_many(db, list(deds))


@router.get("/programas/{id}", response_model=DeduccionRead)
async def detalle_programa(id: int, db: AsyncSession = Depends(get_db)):
    ded = await db.get(Deduccion, id)
    if not ded or ded.origen != "manual":
        raise HTTPException(404, "Programa no encontrado")
    return await _enrich_programa(db, ded)


@router.patch("/programas/grupo/{grupo_id}", response_model=List[DeduccionRead])
async def editar_grupo(
    grupo_id: int,
    payload: DeduccionGrupoUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Recalcula el monto_cuota de las cuotas pendientes del grupo."""
    todas = (
        await db.execute(
            select(Deduccion)
            .where(Deduccion.grupo_id == grupo_id, Deduccion.origen == "manual")
            .order_by(Deduccion.cuota_nro)
        )
    ).scalars().all()
    if not todas:
        raise HTTPException(404, "Grupo no encontrado")

    pendientes = [d for d in todas if d.estado == "pendiente"]
    if not pendientes:
        raise HTTPException(409, "No hay cuotas pendientes editables")

    nuevo_total = Decimal(str(payload.monto_total))
    n = len(pendientes)
    cuota_base = (nuevo_total * 100).to_integral_value(rounding=ROUND_DOWN) / 100 / n
    cuota_base = cuota_base.quantize(Decimal("0.01"))
    resto = nuevo_total - cuota_base * n

    for i, d in enumerate(pendientes):
        monto = cuota_base + (resto if i == n - 1 else Decimal("0"))
        d.monto_cuota = monto
        d.calculado_total = monto
        d.monto_total = nuevo_total

    await db.commit()
    for d in todas:
        await db.refresh(d)

    return await enrich_many(db, list(todas))


@router.patch("/programas/{id}/estado", response_model=DeduccionRead)
async def cambiar_estado_programa(
    id: int,
    payload: DeduccionEstadoPayload,
    db: AsyncSession = Depends(get_db),
):
    ded = await db.get(Deduccion, id)
    if not ded or ded.origen != "manual":
        raise HTTPException(404, "Programa no encontrado")

    actual = ded.estado
    nuevo = payload.estado

    if actual == nuevo:
        raise HTTPException(409, "La cuota ya está en ese estado")
    if actual == "aplicado":
        raise HTTPException(409, "La cuota ya fue aplicada y no puede modificarse")
    if actual == "cancelado":
        raise HTTPException(409, "La cuota está cancelada y no puede modificarse")

    VALIDAS = {
        ("pendiente", "en_pago"),
        ("en_pago", "pendiente"),
        ("pendiente", "cancelado"),
        ("en_pago", "cancelado"),
    }
    if (actual, nuevo) not in VALIDAS:
        raise HTTPException(409, f"Transición no permitida: {actual} → {nuevo}")

    if nuevo == "en_pago":
        pago = await db.scalar(select(Pago).where(Pago.estado == "A").limit(1))
        if not pago:
            raise HTTPException(409, "No hay pago abierto")
        ded.pago_id = pago.id
    else:
        ded.pago_id = None

    ded.estado = nuevo
    await db.commit()
    await db.refresh(ded)
    return await _enrich_programa(db, ded)


@router.delete("/programas/grupo/{grupo_id}")
async def cancelar_grupo(grupo_id: int, db: AsyncSession = Depends(get_db)):
    """Cancela cuotas pendientes y en_pago del grupo."""
    result = await db.execute(
        update(Deduccion)
        .where(
            Deduccion.grupo_id == grupo_id,
            Deduccion.origen == "manual",
            Deduccion.estado.in_(["pendiente", "en_pago"]),
        )
        .values(estado="cancelado", pago_id=None)
    )
    if result.rowcount == 0:
        exists = await db.scalar(
            select(Deduccion.id)
            .where(Deduccion.grupo_id == grupo_id, Deduccion.origen == "manual")
            .limit(1)
        )
        if not exists:
            raise HTTPException(404, "Grupo no encontrado")
    await db.commit()
    return {"canceladas": result.rowcount, "grupo_id": grupo_id}


@router.delete("/programas/{id}", response_model=DeduccionRead)
async def cancelar_programa(id: int, db: AsyncSession = Depends(get_db)):
    ded = await db.get(Deduccion, id)
    if not ded or ded.origen != "manual":
        raise HTTPException(404, "Programa no encontrado")
    if ded.estado == "aplicado":
        raise HTTPException(409, "La cuota ya fue aplicada y no puede cancelarse")

    ded.estado = "cancelado"
    ded.pago_id = None
    await db.commit()
    await db.refresh(ded)
    return await _enrich_programa(db, ded)

# endregion


# region Generar programas en pago

@router.post("/{pago_id}/programas/generar")
async def generar_programas_endpoint(pago_id: int, db: AsyncSession = Depends(get_db)):
    """
    Ejecuta las cuotas manuales en_pago del pago:
    escribe a DeduccionSaldo → marca aplicado.
    """
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")
    if pago.estado == "C":
        raise HTTPException(409, "El pago está cerrado")
    return await generar_programas_en_pago(db, pago_id)

# endregion


# =============================================================================
# SOCIOS-DESCUENTO — CRUD completo
# =============================================================================

async def _enrich_socio(db: AsyncSession, socio: SocioDescuento) -> SocioDescuentoRead:
    """Construye SocioDescuentoRead enriquecido con datos del médico, pagador y descuento."""
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
    )


@router.get("/socios", response_model=List[SocioDescuentoRead])
async def listar_socios_descuento(
    medico_id: Optional[int] = Query(None),
    descuento_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None, description="Busca por nombre del médico (LIKE)"),
    activos_only: bool = Query(False, description="True: solo sin fecha_baja"),
    db: AsyncSession = Depends(get_db),
):
    """Lista SocioDescuento con filtros. Incluye nombre del médico, pagador y descuento."""
    stmt = select(SocioDescuento)

    if medico_id is not None:
        stmt = stmt.where(SocioDescuento.medico_id == medico_id)
    if descuento_id is not None:
        stmt = stmt.where(SocioDescuento.descuento_id == descuento_id)
    if activos_only:
        stmt = stmt.where(SocioDescuento.fecha_baja.is_(None))

    if q and q.strip():
        stmt = stmt.join(
            ListadoMedico, ListadoMedico.ID == SocioDescuento.medico_id
        ).where(ListadoMedico.NOMBRE.ilike(f"%{q.strip()}%"))

    socios = (await db.execute(stmt.order_by(SocioDescuento.id))).scalars().all()
    return [await _enrich_socio(db, s) for s in socios]


@router.get("/socios/{socio_descuento_id}", response_model=SocioDescuentoRead)
async def detalle_socio_descuento(
    socio_descuento_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Devuelve un SocioDescuento por ID enriquecido."""
    socio = await db.get(SocioDescuento, socio_descuento_id)
    if not socio:
        raise HTTPException(404, "SocioDescuento no encontrado")
    return await _enrich_socio(db, socio)


@router.post("/socios", response_model=SocioDescuentoRead, status_code=201)
async def crear_socio_descuento(
    payload: SocioDescuentoCreate,
    db: AsyncSession = Depends(get_db),
):
    """Asigna un descuento/concepto a un médico."""
    if not await db.scalar(select(ListadoMedico.ID).where(ListadoMedico.ID == payload.medico_id)):
        raise HTTPException(404, "Médico no encontrado")
    if not await db.scalar(select(Descuentos.id).where(Descuentos.id == payload.descuento_id)):
        raise HTTPException(404, "Descuento no encontrado")
    if payload.pagador_medico_id is not None:
        if not await db.scalar(select(ListadoMedico.ID).where(ListadoMedico.ID == payload.pagador_medico_id)):
            raise HTTPException(404, "Médico pagador no encontrado")

    from sqlalchemy.exc import IntegrityError
    socio = SocioDescuento(
        medico_id=payload.medico_id,
        descuento_id=payload.descuento_id,
        pagador_medico_id=payload.pagador_medico_id,
        fecha_alta=payload.fecha_alta or datetime.date.today(),
        fecha_baja=payload.fecha_baja,
    )
    db.add(socio)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "El médico ya tiene asignado ese descuento")
    await db.refresh(socio)
    return await _enrich_socio(db, socio)


@router.patch("/socios/{socio_descuento_id}", response_model=SocioDescuentoRead)
async def actualizar_socio_descuento(
    socio_descuento_id: int,
    payload: SocioDescuentoUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    Actualiza descuento_id, pagador_medico_id y/o fecha_baja de un SocioDescuento.
    Solo se modifican los campos explícitamente enviados en el body.
    Para quitar el pagador: enviar `pagador_medico_id: null`.
    """
    socio = await db.get(SocioDescuento, socio_descuento_id)
    if not socio:
        raise HTTPException(404, "SocioDescuento no encontrado")

    sent = payload.model_fields_set

    if "descuento_id" in sent and payload.descuento_id is not None:
        if not await db.scalar(select(Descuentos.id).where(Descuentos.id == payload.descuento_id)):
            raise HTTPException(404, "Descuento no encontrado")
        socio.descuento_id = payload.descuento_id

    if "pagador_medico_id" in sent:
        if payload.pagador_medico_id is not None:
            if not await db.scalar(select(ListadoMedico.ID).where(ListadoMedico.ID == payload.pagador_medico_id)):
                raise HTTPException(404, "Médico pagador no encontrado")
        socio.pagador_medico_id = payload.pagador_medico_id

    if "fecha_baja" in sent:
        socio.fecha_baja = payload.fecha_baja

    await db.commit()
    await db.refresh(socio)
    return await _enrich_socio(db, socio)


@router.delete("/socios/{socio_descuento_id}", status_code=204)
async def eliminar_socio_descuento(
    socio_descuento_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Elimina un SocioDescuento (desasigna el concepto del médico)."""
    socio = await db.get(SocioDescuento, socio_descuento_id)
    if not socio:
        raise HTTPException(404, "SocioDescuento no encontrado")
    await db.delete(socio)
    await db.commit()
    return None


# region Pagador delegado en SocioDescuento (endpoint legacy — usar PATCH /socios/{id})

@router.patch("/socios/{socio_descuento_id}/pagador", response_model=SocioDescuentoRead)
async def set_pagador_socio(
    socio_descuento_id: int,
    payload: SocioDescuentoPagadorUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Actualiza (o quita) el pagador delegado de un SocioDescuento."""
    socio = await db.get(SocioDescuento, socio_descuento_id)
    if not socio:
        raise HTTPException(404, "SocioDescuento no encontrado")

    if payload.pagador_medico_id is not None:
        if not await db.scalar(
            select(ListadoMedico.ID).where(ListadoMedico.ID == payload.pagador_medico_id)
        ):
            raise HTTPException(404, "Médico pagador no encontrado")

    socio.pagador_medico_id = payload.pagador_medico_id
    await db.commit()
    await db.refresh(socio)
    return socio

# endregion


# =============================================================================
# HISTORIAL Y REPORTES
# =============================================================================

@router.get("/historial", response_model=DeduccionHistorialPage)
async def historial_deducciones(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=50),
    medico_id: Optional[int] = Query(None),
    descuento_id: Optional[int] = Query(None, description="Filtrar por concepto/descuento"),
    estado: Optional[str] = Query(None, description="pendiente|en_pago|aplicado|cancelado|vencida"),
    origen: Optional[str] = Query(None, description="manual|automatico"),
    mes_desde: Optional[int] = Query(None, ge=1, le=12),
    anio_desde: Optional[int] = Query(None, ge=2000),
    mes_hasta: Optional[int] = Query(None, ge=1, le=12),
    anio_hasta: Optional[int] = Query(None, ge=2000),
    db: AsyncSession = Depends(get_db),
):
    """
    Vista unificada de todas las deducciones (manual + automatico).
    Estados: pendiente | en_pago | aplicado | cancelado | vencida (derivado para manual vencidas)
    El response incluye `monto_total` con la suma de todos los ítems filtrados (no solo la página).
    """
    return await get_historial_deducciones(
        db,
        page=page,
        size=size,
        medico_id=medico_id,
        descuento_id=descuento_id,
        estado=estado,
        origen=origen,
        mes_desde=mes_desde,
        anio_desde=anio_desde,
        mes_hasta=mes_hasta,
        anio_hasta=anio_hasta,
    )


@router.get("/historial/export", response_model=List[DeduccionHistorialItem])
async def historial_deducciones_export(
    medico_id: Optional[int] = Query(None),
    descuento_id: Optional[int] = Query(None, description="Filtrar por concepto/descuento"),
    estado: Optional[str] = Query(None, description="pendiente|en_pago|aplicado|cancelado|vencida"),
    origen: Optional[str] = Query(None, description="manual|automatico"),
    mes_desde: Optional[int] = Query(None, ge=1, le=12),
    anio_desde: Optional[int] = Query(None, ge=2000),
    mes_hasta: Optional[int] = Query(None, ge=1, le=12),
    anio_hasta: Optional[int] = Query(None, ge=2000),
    db: AsyncSession = Depends(get_db),
):
    """
    Devuelve todos los ítems filtrados sin paginar, para exportación (Excel/PDF).
    Mismos filtros que GET /historial.
    """
    return await get_historial_export(
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


@router.get("/top-deudores", response_model=List[TopDeudorItem])
async def top_deudores(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    return await get_top_deudores(db, limit=limit)


# =============================================================================
# ACCIONES UNIFICADAS — /historial/{id}/...
# Operan sobre la tabla única Deduccion.
# =============================================================================

_ERRORES_ITEM = {
    "not_found":              (404, "Ítem no encontrado"),
    "mismo_estado":           (409, "El ítem ya está en ese estado"),
    "pago_cerrado":           (409, "El pago asociado está cerrado"),
    "no_pago_abierto":        (409, "No hay pago abierto al que asignar"),
    "no_editable:aplicado":   (409, "El ítem ya fue aplicado y no puede modificarse"),
    "no_editable:cancelado":  (409, "El ítem está cancelado y no puede modificarse"),
}


def _handle_item_error(exc: ValueError) -> None:
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


@router.patch("/historial/{id}/estado", response_model=DeduccionHistorialItem)
async def cambiar_estado_item_endpoint(
    id: int,
    payload: DeduccionEstadoPayload,
    db: AsyncSession = Depends(get_db),
):
    """
    Cambia el estado de cualquier deducción (manual o automática).
    - pendiente / vencida → en_pago  (asigna al pago abierto)
    - en_pago → pendiente            (quita del pago)
    - pendiente / en_pago → cancelado
    No aplica a estados terminales (aplicado, cancelado).
    """
    try:
        return await cambiar_estado_item(db, id, payload.estado)
    except ValueError as exc:
        _handle_item_error(exc)


@router.patch("/historial/{id}/monto", response_model=DeduccionHistorialItem)
async def cambiar_monto_item_endpoint(
    id: int,
    payload: DeduccionItemMontoPayload,
    db: AsyncSession = Depends(get_db),
):
    """
    Edita el monto de cualquier deducción mientras el pago esté abierto.
    Para manuales: actualiza monto_cuota + calculado_total.
    Para automáticas: actualiza calculado_total.
    """
    try:
        return await cambiar_monto_item(db, id, payload.monto)
    except ValueError as exc:
        _handle_item_error(exc)


@router.delete("/historial/{id}", response_model=DeduccionItemEliminarResponse)
async def eliminar_item_endpoint(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Elimina / cancela una deducción.
    - Manual → estado = 'cancelado' (historial conservado).
    - Automática → borrado físico.
    Requiere pago abierto. No aplica a ítems ya aplicados.
    """
    try:
        return await eliminar_item(db, id)
    except ValueError as exc:
        _handle_item_error(exc)


# =============================================================================
# VERIFICAR Y DESHACER DESCUENTOS POR PAGO
# =============================================================================

@router.get("/por_pago/{pago_id}", response_model=DeduccionPorPagoResponse)
async def verificar_deducciones_pago(
    pago_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Verifica si existen deducciones asociadas al pago indicado.
    Devuelve `existe`, el total de ítems y el monto acumulado.
    """
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")
    return await verificar_deducciones_por_pago(db, pago_id)


@router.delete("/{pago_id}/colegio/deshacer", response_model=DeshacerDescuentosResponse)
async def deshacer_descuentos_pago(
    pago_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Elimina todos los descuentos generados automáticamente para el pago (origen='automatico',
    estado='en_pago') y revierte los saldos en DeduccionSaldo.
    Solo opera si el pago está abierto (estado='A').
    """
    try:
        return await deshacer_descuentos_generados(db, pago_id)
    except ValueError as exc:
        msg = str(exc)
        if msg == "pago_no_encontrado":
            raise HTTPException(404, "Pago no encontrado")
        if msg == "pago_cerrado":
            raise HTTPException(409, "El pago está cerrado y no puede deshacerse")
