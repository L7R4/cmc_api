import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import (
    Liquidacion,
    Pago,
    Recibo,
)
from app.modules.pagos.schemas import (
    EditarEstadoRecibosPayload,
    EliminarRecibosPayload,
    GenerarRecibosPayload,
    InformePagoRead,
    PagoCreate,
    PagoRead,
    PagoUpdate,
    PagoVistaPreviaRead,
    ReciboRead,
    TipoInforme,
)
from app.modules.deducciones.service import (
    aplicar_deducciones_al_cierre,
)
from app.modules.pagos.service import (
    generar_recibo_medico,
    generar_todos_recibos,
    informe_pago,
    recalcular_totales_pago,
    refrescar_detalle_medico,
    refrescar_todos_medicos,
    vista_previa_pago,
)
from app.services.deducciones_update import (
    marcar_deducciones_aplicadas,
    revertir_deducciones_al_reabrir,
)
from app.services.lote_ajuste_update import (
    marcar_lotes_aplicados,
    revertir_lotes_al_reabrir,
)
from app.services.deducciones_rollback import rollback_deducciones_pago
from app.services.lote_ajuste_rollback import rollback_lotes_pago
from app.services.liquidaciones_rollback import rollback_liquidaciones_pago, rollback_recibos_pago

router = APIRouter()


def _enrich_pago(pago: Pago, totales: dict) -> PagoRead:
    return PagoRead(
        id=pago.id,
        anio=pago.anio,
        mes=pago.mes,
        descripcion=pago.descripcion,
        estado=pago.estado,
        cierre_timestamp=pago.cierre_timestamp,
        deducciones_dirty=pago.deducciones_dirty,
        total_bruto=totales["total_bruto"],
        total_debitos=totales["total_debitos"],
        total_creditos=totales["total_creditos"],
        total_neto=totales["total_neto"],
        total_deduccion=totales["total_deduccion"],
    )


# ================================================
# GET /pagos — Listar pagos
# ================================================
@router.get("/", response_model=List[PagoRead])
async def listar_pagos(
    anio: Optional[int] = Query(None, ge=1900, le=3000),
    mes: Optional[int] = Query(None, ge=1, le=12),
    estado: Optional[str] = Query(None, description="A o C"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Pago).order_by(Pago.anio.desc(), Pago.mes.desc(), Pago.id.desc())
    if anio is not None:
        stmt = stmt.where(Pago.anio == anio)
    if mes is not None:
        stmt = stmt.where(Pago.mes == mes)
    if estado is not None:
        stmt = stmt.where(Pago.estado == estado)
    stmt = stmt.offset(skip).limit(limit)

    pagos = (await db.execute(stmt)).scalars().all()

    result = []
    for pago in pagos:
        totales = await recalcular_totales_pago(db, pago.id)
        result.append(_enrich_pago(pago, totales))
    return result


# ================================================
# POST /pagos — Crear pago
# ================================================
@router.post("/", response_model=PagoRead, status_code=201)
async def crear_pago(payload: PagoCreate, db: AsyncSession = Depends(get_db)):
    # 409 si ya hay pago en estado='A'
    existing_open = (await db.execute(
        select(Pago.id).where(Pago.estado == "A").limit(1)
    )).first()
    if existing_open:
        raise HTTPException(
            409,
            detail={
                "reason": "pago_abierto_existe",
                "pago_id": existing_open[0],
                "message": "Ya existe un pago en estado abierto. Ciérrelo antes de crear uno nuevo.",
            },
        )

    obj = Pago(anio=payload.anio, mes=payload.mes, descripcion=payload.descripcion)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)

    totales = await recalcular_totales_pago(db, obj.id)
    return _enrich_pago(obj, totales)


# ================================================
# GET /pagos/{pago_id} — Obtener pago
# ================================================
@router.get("/{pago_id}", response_model=PagoRead)
async def obtener_pago(pago_id: int, db: AsyncSession = Depends(get_db)):
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")
    totales = await recalcular_totales_pago(db, pago_id)
    return _enrich_pago(pago, totales)


# ================================================
# PUT /pagos/{pago_id} — Editar pago
# ================================================
@router.put("/{pago_id}", response_model=PagoRead)
async def editar_pago(pago_id: int, payload: PagoUpdate, db: AsyncSession = Depends(get_db)):
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")
    if pago.estado == "C":
        raise HTTPException(409, "No se puede editar un pago cerrado")

    if payload.descripcion is not None:
        pago.descripcion = payload.descripcion

    await db.commit()
    await db.refresh(pago)
    totales = await recalcular_totales_pago(db, pago_id)
    return _enrich_pago(pago, totales)


# ================================================
# DELETE /pagos/{pago_id} — Eliminar pago
# ================================================
@router.delete("/{pago_id}", status_code=204)
async def eliminar_pago(pago_id: int, db: AsyncSession = Depends(get_db)):
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")

    # Bloquear si hay recibos ya cobrados (representan pagos reales efectuados)
    rec_pagados = (await db.execute(
        select(func.count(Recibo.id)).where(
            Recibo.pago_id == pago_id,
            Recibo.estado == "pagado",
        )
    )).scalar_one()
    if rec_pagados > 0:
        raise HTTPException(
            409,
            detail={
                "reason": "recibos_pagados",
                "cantidad": rec_pagados,
                "message": "No se puede eliminar un pago con recibos ya cobrados.",
            },
        )

    # Rollback en orden respetando FK constraints:
    # 1. Deducciones: elimina DeduccionAplicacion, revierte estado según tipo
    await rollback_deducciones_pago(db, pago_id)

    # 2. Lotes: desvincula LoteAjuste (L → C, pago_id → NULL)
    await rollback_lotes_pago(db, pago_id)

    # 3. Recibos (emitidos/pendientes): eliminar antes que Liquidacion por FK RESTRICT
    await rollback_recibos_pago(db, pago_id)

    # 4. Liquidaciones y sus detalles
    await rollback_liquidaciones_pago(db, pago_id)

    # 5. Eliminar Pago (PagoMedico se elimina por CASCADE automáticamente)
    await db.delete(pago)
    await db.commit()
    return None


# ================================================
# POST /pagos/{pago_id}/cerrar — Cerrar pago
# ================================================
@router.post("/{pago_id}/cerrar", status_code=200)
async def cerrar_pago(pago_id: int, db: AsyncSession = Depends(get_db)):
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")
    if pago.estado == "C":
        raise HTTPException(409, "El pago ya está cerrado")

    # 1. Aplicar deducciones (greedy: mayor primero por médico) — marca aplicado/pendiente
    await aplicar_deducciones_al_cierre(db, pago_id)

    # 2. Red de seguridad: cualquier Deduccion en_pago que haya escapado el paso anterior
    ded_extra = await marcar_deducciones_aplicadas(db, pago_id)

    # 3. Marcar lotes: L → AP (Aplicado — inmutables desde ahora)
    lotes_info = await marcar_lotes_aplicados(db, pago_id)

    pago.estado = "C"
    pago.cierre_timestamp = datetime.datetime.now()
    await db.commit()
    await db.refresh(pago)
    totales = await recalcular_totales_pago(db, pago_id)
    result = _enrich_pago(pago, totales)
    return {
        **result.model_dump(),
        "cierre_info": {
            **lotes_info,
            "deducciones_aplicadas_extra": ded_extra,
        },
    }


# ================================================
# POST /pagos/{pago_id}/reabrir — Reabrir pago
# ================================================
@router.post("/{pago_id}/reabrir", response_model=PagoRead)
async def reabrir_pago(pago_id: int, db: AsyncSession = Depends(get_db)):
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")
    if pago.estado == "A":
        raise HTTPException(409, "El pago ya está abierto")

    # 409 si hay recibos emitidos o pagados
    rec_activos = (await db.execute(
        select(func.count(Recibo.id)).where(
            Recibo.pago_id == pago_id,
            Recibo.estado.in_(["emitido", "pagado"]),
        )
    )).scalar_one()
    if rec_activos > 0:
        raise HTTPException(409, "No se puede reabrir un pago con recibos emitidos o pagados")

    # 409 si ya hay otro pago abierto
    otro_abierto = (await db.execute(
        select(Pago.id).where(Pago.estado == "A", Pago.id != pago_id).limit(1)
    )).first()
    if otro_abierto:
        raise HTTPException(409, f"Ya existe otro pago abierto (id={otro_abierto[0]})")

    # Revertir deducciones: aplicado → en_pago (re-evaluadas en el próximo cierre)
    await revertir_deducciones_al_reabrir(db, pago_id)

    # Revertir lotes: AP → L (vuelven a "En liquidaciones")
    await revertir_lotes_al_reabrir(db, pago_id)

    pago.estado = "A"
    pago.cierre_timestamp = None
    await db.commit()
    await db.refresh(pago)
    totales = await recalcular_totales_pago(db, pago_id)
    return _enrich_pago(pago, totales)


# ================================================
# GET /pagos/{pago_id}/pago_medico_actualizado
# ================================================
@router.get("/{pago_id}/pago_medico_actualizado", status_code=200)
async def obtener_pago_actualizado(
    pago_id: int,
    medico_id: Optional[int] = Query(
        None,
        description="ID (PK interna) del médico. Si se omite, refresca y devuelve todos.",
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Recalcula y persiste PagoMedico para uno o todos los médicos del pago.
    Devuelve {medico_id: {info_medico, resumen, detalle}} por cada médico procesado.
    """
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")

    if medico_id is not None:
        result = await refrescar_detalle_medico(db, pago_id, medico_id, pago=pago)
    else:
        result = await refrescar_todos_medicos(db, pago_id, pago=pago)

    await db.commit()
    return result


# ================================================
# GET /pagos/{pago_id}/vista_previa — Vista previa del pago
# ================================================
@router.get("/{pago_id}/vista_previa", response_model=PagoVistaPreviaRead)
async def vista_previa_pago_endpoint(pago_id: int, db: AsyncSession = Depends(get_db)):
    """
    Vista previa resumida del pago con tres secciones:
    - liquidaciones: facturas con totales (bruto, débitos, créditos, reconocido, neto) + grand-total
    - deducciones: items en estado 'en_pago' (abierto) o 'aplicado' (cerrado) + grand-total
    - lotes: ajustes vinculados con totales de débito/crédito + grand-total
    """
    return await vista_previa_pago(db, pago_id)


# ================================================
# POST /pagos/{pago_id}/recibos/generar — Generar/actualizar recibos
# ================================================
@router.post("/{pago_id}/recibos/generar", response_model=List[ReciboRead], status_code=200)
async def generar_recibos_endpoint(
    pago_id: int,
    payload: Optional[GenerarRecibosPayload] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Genera o actualiza recibos copiando el detalle_json desde PagoMedico.
    Si se envía medico_ids en el body, solo procesa esos médicos; de lo contrario, todos.
    """
    pago = await db.get(Pago, pago_id)
    if not pago:
        raise HTTPException(404, "Pago no encontrado")

    medico_ids = payload.medico_ids if payload else None
    recibos = await generar_todos_recibos(db, pago_id, medico_ids=medico_ids, pago=pago)
    await db.commit()
    for r in recibos:
        await db.refresh(r)
    return recibos


# ================================================
# GET /pagos/{pago_id}/recibos — Listar recibos
# ================================================
@router.get("/{pago_id}/recibos", response_model=List[ReciboRead])
async def listar_recibos(
    pago_id: int,
    medico_id: Optional[int] = Query(None, description="Filtra por ID (PK) del médico"),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Recibo).where(Recibo.pago_id == pago_id)
    if medico_id is not None:
        stmt = stmt.where(Recibo.medico_id == medico_id)
    stmt = stmt.order_by(Recibo.medico_id)
    rows = (await db.execute(stmt)).scalars().all()
    return rows


# ================================================
# POST /pagos/{pago_id}/recibos/emitir_todos
# ================================================
@router.post("/{pago_id}/recibos/emitir_todos", response_model=List[ReciboRead])
async def emitir_todos_recibos(pago_id: int, db: AsyncSession = Depends(get_db)):
    """Marca como 'emitido' todos los recibos del pago que no estén en estado 'pagado'."""
    rows = (await db.execute(
        select(Recibo).where(
            Recibo.pago_id == pago_id,
            Recibo.estado != "pagado",
        )
    )).scalars().all()

    if not rows:
        raise HTTPException(404, "No se encontraron recibos para emitir en este pago")

    for recibo in rows:
        recibo.estado = "emitido"

    await db.commit()
    for r in rows:
        await db.refresh(r)
    return rows


# ================================================
# PATCH /pagos/{pago_id}/recibos/estado — Editar estado de recibos (bulk)
# ================================================
@router.patch("/{pago_id}/recibos/estado", response_model=List[ReciboRead])
async def editar_estado_recibos(
    pago_id: int,
    payload: EditarEstadoRecibosPayload,
    db: AsyncSession = Depends(get_db),
):
    """
    Cambia el estado de una lista de recibos.
    Estados válidos: en_revision, liquidado, emitido.
    """
    rows = (await db.execute(
        select(Recibo).where(
            Recibo.pago_id == pago_id,
            Recibo.id.in_(payload.recibo_ids),
        )
    )).scalars().all()

    if not rows:
        raise HTTPException(404, "No se encontraron recibos con los IDs indicados en este pago")

    for recibo in rows:
        recibo.estado = payload.estado

    await db.commit()
    for r in rows:
        await db.refresh(r)
    return rows


# ================================================
# DELETE /pagos/{pago_id}/recibos — Eliminar recibos (bulk)
# ================================================
@router.delete("/{pago_id}/recibos", status_code=200)
async def eliminar_recibos(
    pago_id: int,
    payload: EliminarRecibosPayload,
    db: AsyncSession = Depends(get_db),
):
    """
    Elimina una lista de recibos por ID.
    No se pueden eliminar recibos en estado 'pagado'.
    """
    rows = (await db.execute(
        select(Recibo).where(
            Recibo.pago_id == pago_id,
            Recibo.id.in_(payload.recibo_ids),
        )
    )).scalars().all()

    if not rows:
        raise HTTPException(404, "No se encontraron recibos con los IDs indicados en este pago")

    pagados = [r.id for r in rows if r.estado == "pagado"]
    if pagados:
        raise HTTPException(
            409,
            detail={
                "reason": "recibos_pagados",
                "recibo_ids": pagados,
                "message": "No se pueden eliminar recibos en estado 'pagado'.",
            },
        )

    eliminados = [r.id for r in rows]
    for recibo in rows:
        await db.delete(recibo)
    await db.commit()
    return {"eliminados": eliminados, "total": len(eliminados)}


# ================================================
# GET /pagos/{pago_id}/informe/{tipo} — Informe por tipo de pago
# ================================================
@router.get("/{pago_id}/informe/{tipo}", response_model=InformePagoRead)
async def informe_pago_endpoint(
    pago_id: int,
    tipo: TipoInforme,
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna los médicos que participaron en el pago filtrados por tipo:
    - santander:    CBU empieza con 072
    - otros_bancos: tiene CBU pero no empieza con 072
    - cheques:      sin CBU (se paga por cheque)
    - cuit_30:      CUIT comienza con 30 (personas jurídicas)
    """
    return await informe_pago(db, pago_id, tipo)
