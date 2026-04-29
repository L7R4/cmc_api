import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession
from decimal import ROUND_HALF_UP, Decimal

from app.db.database import get_db
from app.db.models import (
    Deduccion,
    Descuentos,
    ListadoMedico,
    Pago,
    SocioDescuento,
)
from app.modules.deducciones.helpers import (
    NRO_COLEGIO_CONTRIB_GASTOS,
    NRO_COLEGIO_CONTRIB_HONORARIOS,
    TWOPLACES,
    _base_bruto_por_medico_en_pago,
    _calc_monto,
    _enrich_socio,
    _gastos_por_medico_en_pago,
    _get_descuento,
    _handle_item_error,
    _honorarios_por_medico_en_pago,
    _medicos_para_descuento,
)
from app.modules.deducciones.schemas import (
    DeduccionCreate,
    DeduccionHistorialItem,
    DeduccionHistorialPage,
    DeduccionItemEliminarResponse,
    DeduccionPorPagoResponse,
    DeduccionRead,
    DeduccionUpdate,
    DeduccionesAplicadasResponse,
    DeshacerDescuentosResponse,
    SocioDescuentoCreate,
    SocioDescuentoPagadorUpdate,
    SocioDescuentoRead,
    SocioDescuentoUpdate,
    TopDeudorItem,
)
from app.modules.deducciones.service import (
    _ded_to_historial,
    _recalcular_montos_aplicados_en_pago,
    cambiar_estado_item,
    cambiar_monto_item,
    crear_programa,
    deshacer_descuentos_generados,
    eliminar_item,
    get_deducciones_aplicadas,
    get_historial_deducciones,
    get_historial_export,
    get_top_deudores,
    marcar_deducciones_dirty,
    pagar_deduccion,
    refrescar_deducciones_pago,
    verificar_deducciones_por_pago,
)

router = APIRouter()

# =============================================================================
# DEDUCCIONES AUTOMATICAS — Generación masiva por descuento
# =============================================================================
@router.post("/{pago_id}/colegio/bulk_generar_descuento/{desc_id}")
async def bulk_generar_descuento(
    pago_id: int, desc_id: int, db: AsyncSession = Depends(get_db)
):
    """
    Para cada médico asignado al descuento (vía socio_descuento):
      1. Enrola las deducciones 'pendiente' con period <= pago → pasan a 'en_pago'.
      2. Crea nueva deducción para el período actual si el médico no tiene ninguna
         activa (no-eliminado) para ese descuento+período.
    - paga_por_caja=False → en_pago, generado_en_pago_id=pago_id
    - paga_por_caja=True  → pendiente (se cobra en caja)
    Para descuentos aplica_a_todos solo se crean nuevas (no hay pendientes previas).
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
        med_tuples: list[tuple[int, bool]] = [
            (mid, False) for mid, bruto in base_por_med.items() if bruto > 0
        ]
    else:
        med_tuples = await _medicos_para_descuento(db, desc_id)

    if not med_tuples:
        return {"enrolados": 0, "generados": 0, "omitidos": 0, "cargado_total": 0}

    precio = Decimal(str(getattr(desc, "precio", 0) or 0))
    porcentaje = Decimal(str(getattr(desc, "porcentaje", 0) or 0))

    # ── Paso 1: Enrolar pendientes con period <= pago — solo médicos en el pago ──
    enrolados = 0
    if not getattr(desc, "aplica_a_todos", False):
        med_ids_false_en_pago = [mid for mid, paga_caja in med_tuples if not paga_caja and mid in base_por_med]
        if med_ids_false_en_pago:
            rows_a_enrolar = (await db.execute(
                select(Deduccion).where(
                    Deduccion.descuento_id == desc_id,
                    Deduccion.medico_id.in_(med_ids_false_en_pago),
                    Deduccion.estado == "pendiente",
                    Deduccion.paga_por_caja == False,
                    or_(
                        Deduccion.anio_aplicar < pago.anio,
                        and_(
                            Deduccion.anio_aplicar == pago.anio,
                            Deduccion.mes_aplicar <= pago.mes,
                        ),
                    ),
                )
            )).scalars().all()
            if rows_a_enrolar:
                await db.execute(
                    update(Deduccion)
                    .where(Deduccion.id.in_([d.id for d in rows_a_enrolar]))
                    .values(estado="en_pago")
                    # generado_en_pago_id se deja intacto (NULL):
                    # estas deducciones existían antes del pago, solo se enrolan.
                    # deshacer_descuentos_generados las revierte a 'pendiente'.
                )
                enrolados = len(rows_a_enrolar)
                await db.flush()

    # ── Paso 2: Crear nuevas deducciones para el período actual (todos los médicos)
    # luego enrolar solo los que participan en el pago (mid in base_por_med) ──
    to_insert = []
    for mid, paga_caja in med_tuples:
        base = base_por_med.get(mid, Decimal(0))
        monto, porc_usado = _calc_monto(base, precio, porcentaje)
        if monto <= 0:
            continue

        en_pago_ahora = not paga_caja and mid in base_por_med
        porc_ap = (porc_usado if porc_usado else Decimal("0")).quantize(
            TWOPLACES, rounding=ROUND_HALF_UP
        )
        to_insert.append({
            "medico_id": mid,
            "descuento_id": desc_id,
            "calculado_total": monto,
            "porcentaje_aplicado": porc_ap,
            "monto_aplicado": Decimal("0.00"),
            "origen": "automatico",
            "paga_por_caja": paga_caja,
            "estado": "en_pago" if en_pago_ahora else "pendiente",
            "cuota_nro": 0,
            "mes_aplicar": pago.mes,
            "anio_aplicar": pago.anio,
            "generado_en_pago_id": pago_id if en_pago_ahora else None,
        })

    # Omitir médicos que ya tienen deducción activa para este descuento+período actual.
    omitidos = 0
    if to_insert:
        med_ids_a_insertar = [r["medico_id"] for r in to_insert]
        ya_activos: set[int] = set(
            (await db.execute(
                select(Deduccion.medico_id)
                .where(
                    Deduccion.descuento_id == desc_id,
                    Deduccion.mes_aplicar == pago.mes,
                    Deduccion.anio_aplicar == pago.anio,
                    Deduccion.medico_id.in_(med_ids_a_insertar),
                    Deduccion.estado != "eliminado",
                )
            )).scalars().all()
        )
        to_insert = [r for r in to_insert if r["medico_id"] not in ya_activos]
        omitidos = len(ya_activos)

    if not to_insert and enrolados == 0:
        return {"enrolados": 0, "generados": 0, "omitidos": omitidos, "cargado_total": 0}

    total_cargado = sum(r["calculado_total"] for r in to_insert)

    if to_insert:
        await db.execute(mysql_insert(Deduccion.__table__).values(to_insert))

    await db.flush()
    await _recalcular_montos_aplicados_en_pago(db, pago_id)
    await marcar_deducciones_dirty(db, pago_id)
    await db.commit()

    return {
        "enrolados": enrolados,
        "generados": len(to_insert),
        "omitidos": omitidos,
        "cargado_total": float(Decimal(str(total_cargado)).quantize(TWOPLACES)),
    }


@router.post("/{pago_id}/deducciones/refrescar", status_code=status.HTTP_200_OK)
async def refrescar_deducciones_endpoint(pago_id: int, db: AsyncSession = Depends(get_db)):
    """
    Refresca deducciones del pago:
    1. Auto-enrola manuales pendientes cuyo período <= pago y no pagan por caja.
    2. Recalcula calculado_total de automáticas con porcentaje.
    3. Resetea deducciones_dirty = False.
    """
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")
    if pago.estado == "C":
        raise HTTPException(409, "El pago está cerrado")

    result = await refrescar_deducciones_pago(db, pago)
    return result


# =============================================================================
# SOCIOS-DESCUENTO — CRUD completo
# =============================================================================

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
        paga_por_caja=payload.paga_por_caja,
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

    if "paga_por_caja" in sent and payload.paga_por_caja is not None:
        socio.paga_por_caja = payload.paga_por_caja

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



# =============================================================================
# DEDUCCIONES — Lista, detalle y CRUD unificado (manual + automático)
# =============================================================================

@router.post("/", response_model=List[DeduccionRead], status_code=201)
async def crear_deduccion_manual(
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


@router.get("/", response_model=DeduccionHistorialPage)
async def listar_deducciones(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=50),
    medico_id: Optional[int] = Query(None),
    descuento_id: Optional[int] = Query(None),
    estado: Optional[str] = Query(None, description="pendiente|en_pago|aplicado|cancelado|vencida"),
    origen: Optional[str] = Query(None, description="manual|automatico"),
    paga_por_caja: Optional[bool] = Query(None, description="true=solo caja | false=solo liquidación"),
    mes_desde: Optional[int] = Query(None, ge=1, le=12),
    anio_desde: Optional[int] = Query(None, ge=2000),
    mes_hasta: Optional[int] = Query(None, ge=1, le=12),
    anio_hasta: Optional[int] = Query(None, ge=2000),
    db: AsyncSession = Depends(get_db),
):
    """Lista paginada de deducciones (manual + automático). Incluye monto_total agregado."""
    return await get_historial_deducciones(
        db,
        page=page,
        size=size,
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


@router.get("/export", response_model=List[DeduccionHistorialItem])
async def exportar_deducciones(
    medico_id: Optional[int] = Query(None),
    descuento_id: Optional[int] = Query(None),
    estado: Optional[str] = Query(None, description="pendiente|en_pago|aplicado|cancelado|vencida"),
    origen: Optional[str] = Query(None, description="manual|automatico"),
    paga_por_caja: Optional[bool] = Query(None, description="true=solo caja | false=solo liquidación"),
    mes_desde: Optional[int] = Query(None, ge=1, le=12),
    anio_desde: Optional[int] = Query(None, ge=2000),
    mes_hasta: Optional[int] = Query(None, ge=1, le=12),
    anio_hasta: Optional[int] = Query(None, ge=2000),
    db: AsyncSession = Depends(get_db),
):
    """Sin paginación — para exportar a Excel/PDF."""
    return await get_historial_export(
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


@router.get("/{id}", response_model=DeduccionHistorialItem)
async def detalle_deduccion(id: int, db: AsyncSession = Depends(get_db)):
    ded = await db.get(Deduccion, id)
    if not ded:
        raise HTTPException(404, "Ítem no encontrado")
    return await _ded_to_historial(db, ded)


@router.patch("/{id}", response_model=DeduccionHistorialItem)
async def actualizar_deduccion(
    id: int,
    payload: DeduccionUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    Edita estado, monto y/o paga_por_caja de una deducción. Enviar al menos uno.
    - `estado`: pendiente | en_pago | aplicado | cancelado
    - `monto`: nuevo monto (gt=0)
    - `paga_por_caja`: true | false
    Si se envían varios campos, el orden de aplicación es: monto → paga_por_caja → estado.
    """
    if payload.estado is None and payload.monto is None and payload.paga_por_caja is None:
        raise HTTPException(422, "Debe enviar al menos 'estado', 'monto' o 'paga_por_caja'")
    try:
        ded = None
        if payload.monto is not None:
            ded = await cambiar_monto_item(db, id, payload.monto)
        if payload.paga_por_caja is not None:
            item = await db.get(Deduccion, id)
            if not item:
                raise HTTPException(404, "Ítem no encontrado")
            if item.estado in ("cancelado", "eliminado"):
                raise HTTPException(409, f"El ítem está {item.estado} y no puede modificarse")
            item.paga_por_caja = payload.paga_por_caja
            await db.commit()
            await db.refresh(item)
            ded = await _ded_to_historial(db, item)
        if payload.estado is not None:
            ded = await cambiar_estado_item(db, id, payload.estado)
        return ded
    except ValueError as exc:
        _handle_item_error(exc)


@router.delete("/{id}", response_model=DeduccionItemEliminarResponse)
async def eliminar_deduccion(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Elimina / cancela una deducción.
    - Manual → estado = 'cancelado' (registro conservado).
    - Automática → borrado físico.
    No aplica a ítems ya aplicados.
    """
    try:
        return await eliminar_item(db, id)
    except ValueError as exc:
        _handle_item_error(exc)


@router.post("/{id}/pagar", response_model=DeduccionHistorialItem)
async def pagar_deduccion_endpoint(id: int, db: AsyncSession = Depends(get_db)):
    """
    Marca la deducción como pagada en caja (paga_por_caja=True).
    Fuerza origen='manual', estado='aplicado' y crea DeduccionAplicacion(pago_id=None).
    Si estaba en_pago, la saca del pago antes de aplicar.
    No requiere pago abierto.
    """
    try:
        return await pagar_deduccion(db, id)
    except ValueError as exc:
        _handle_item_error(exc)


@router.get("/top-deudores", response_model=List[TopDeudorItem])
async def top_deudores(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    return await get_top_deudores(db, limit=limit)


# =============================================================================
# VERIFICAR Y DESHACER DESCUENTOS POR PAGO
# =============================================================================

@router.get("/por_pago/{pago_id}/aplicadas", response_model=DeduccionesAplicadasResponse)
async def deducciones_aplicadas_por_pago(
    pago_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Devuelve las deducciones con monto efectivamente aplicado en un pago.

    - **Pago abierto**: lee desde `deducciones` (estado='en_pago', monto_aplicado != 0).
    - **Pago cerrado**: lee desde `deduccion_aplicacion` (campo `aplicado`).
    """
    return await get_deducciones_aplicadas(db, pago_id)


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
    Deshace todos los descuentos del pago:
    - Elimina físicamente las creadas con generado_en_pago_id = pago_id.
    - Revierte a 'pendiente' las enroladas (en_pago sin generado_en_pago_id).
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


@router.delete("/{pago_id}/colegio/deshacer/{desc_id}", response_model=DeshacerDescuentosResponse)
async def deshacer_descuentos_pago_por_descuento(
    pago_id: int,
    desc_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Deshace un descuento específico del pago:
    - Elimina físicamente las creadas con generado_en_pago_id = pago_id y descuento_id = desc_id.
    - Revierte a 'pendiente' las enroladas para ese descuento (en_pago sin generado_en_pago_id).
    Solo opera si el pago está abierto (estado='A').
    """
    try:
        return await deshacer_descuentos_generados(db, pago_id, desc_id=desc_id)
    except ValueError as exc:
        msg = str(exc)
        if msg == "pago_no_encontrado":
            raise HTTPException(404, "Pago no encontrado")
        if msg == "pago_cerrado":
            raise HTTPException(409, "El pago está cerrado y no puede deshacerse")
