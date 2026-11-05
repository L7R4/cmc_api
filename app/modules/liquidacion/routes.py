import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import (
    DetalleLiquidacion,
    FacturacionCMC,
    Liquidacion,
    ObrasSociales,
    Pago,
    Recibo,
)
from app.modules.liquidacion.schemas import (
    DetalleLiquidacionRead,
    DetalleVistaRow,
    LiquidacionCreate,
    LiquidacionPorcentajeUpdate,
    LiquidacionRead,
    ReciboRead,
)
from app.modules.liquidacion.service import (
    aplicar_porcentaje_pago,
    build_detalles_from_cmc,
    lotes_sin_cobertura,
    porcentaje_pago_sugerido,
    sacar_lotes_del_pago,
    validar_porcentaje_pago,
    detalle_recibo_medico,
    recalcular_totales_de_liquidacion,
    vista_detalles_liquidacion,
)
from app.modules.deducciones.service import generar_y_recalcular_porcentuales
from app.modules.pagos.service import refrescar_detalle_medico

router = APIRouter()


# ================================================
# POST: Agregar una factura al pago
# ================================================
@router.post("/liquidaciones_por_os/crear", response_model=LiquidacionRead, status_code=201)
async def crear_liquidacion(payload: LiquidacionCreate, db: AsyncSession = Depends(get_db)):
    """Agrega al pago una factura (original o complementaria) con sus prestaciones cerradas."""
    pago = await db.get(Pago, payload.pago_id)
    if not pago:
        raise HTTPException(400, "pago_id inválido")
    if pago.estado != "A":
        raise HTTPException(409, "No se puede crear una liquidación en un pago cerrado")
    porcentaje_pago = validar_porcentaje_pago(payload.porcentaje_pago)

    factura = await db.get(FacturacionCMC, payload.factura_id)
    if not factura or str(factura.cod_obr) != str(payload.obra_social_id):
        raise HTTPException(404, "La factura no existe o no es de esa obra social")
    if factura.estado not in ("C", "L", "LC"):
        raise HTTPException(409, "La factura todavía no está cerrada")
    periodo = str(factura.periodo or "")
    if len(periodo) != 6 or not periodo.isdigit():
        raise HTTPException(422, f"La factura tiene un período inválido: '{periodo}'")

    existing = (await db.execute(
        select(Liquidacion.id).where(
            Liquidacion.pago_id == payload.pago_id,
            Liquidacion.factura_id == factura.id_prestaciones,
        ).limit(1)
    )).first()
    if existing:
        raise HTTPException(409, "Esa factura ya está en el pago")

    liq = Liquidacion(
        pago_id=payload.pago_id,
        obra_social_id=payload.obra_social_id,
        mes_periodo=int(periodo[4:]),
        anio_periodo=int(periodo[:4]),
        nro_factura=factura.nro_factura,
        factura_id=factura.id_prestaciones,
        factura_version=factura.version,
        total_honorarios=Decimal("0"),
        total_gastos=Decimal("0"),
        total_bruto=Decimal("0"),
        total_debitos=Decimal("0"),
        total_creditos=Decimal("0"),
        total_neto=Decimal("0"),
    )
    db.add(liq)
    await db.flush()

    resumen = await build_detalles_from_cmc(db, liq.id)
    if not resumen["cargadas"]:
        await db.rollback()
        motivos = []
        if resumen["sin_medico"]:
            motivos.append(f"{resumen['sin_medico_cantidad']} sin médico en el padrón (socios {', '.join(map(str, resumen['sin_medico'][:10]))})")
        if resumen["importe_cero"]:
            motivos.append(f"{resumen['importe_cero']} con importe en 0")
        if resumen["ya_liquidadas"]:
            motivos.append(f"{resumen['ya_liquidadas']} ya liquidadas en otro pago")
        if resumen["reservadas"]:
            adelantos = ", ".join(f"#{a}" for a in resumen["adelantos_reservantes"])
            motivos.append(f"{resumen['reservadas']} reservadas en adelantos ({adelantos})")
        raise HTTPException(
            409,
            "La factura no tiene prestaciones para liquidar"
            + (f": {'; '.join(motivos)}." if motivos else "."),
        )

    if porcentaje_pago != Decimal("100"):
        await aplicar_porcentaje_pago(db, liq, porcentaje_pago)

    await recalcular_totales_de_liquidacion(db, liq.id)
    await db.flush()
    await generar_y_recalcular_porcentuales(db, payload.pago_id)

    await db.commit()
    await db.refresh(liq)
    return LiquidacionRead.model_validate(liq).model_copy(update={"resumen_carga": resumen})


# ================================================
# GET: Porcentaje de pago sugerido para una obra social
# ================================================
@router.get("/liquidaciones_por_os/porcentaje_sugerido")
async def obtener_porcentaje_sugerido(
    obra_social_id: int = Query(..., description="NRO_OBRASOCIAL"), db: AsyncSession = Depends(get_db),
):
    """El último porcentaje con que se pagó una factura de esa obra social (100 si nunca se pagó).
    Es sólo una sugerencia: el porcentaje se elige cada vez."""
    return {"obra_social_id": obra_social_id, "porcentaje_pago": await porcentaje_pago_sugerido(db, obra_social_id)}


# ================================================
# PATCH: Cambia el porcentaje de pago de una factura del pago
# ================================================
@router.patch("/liquidaciones_por_os/{liquidacion_id}/porcentaje", response_model=LiquidacionRead)
async def cambiar_porcentaje_pago(
    liquidacion_id: int, payload: LiquidacionPorcentajeUpdate, db: AsyncSession = Depends(get_db),
):
    """Recalcula la factura (renglones y ajustes) sobre lo facturado con el nuevo porcentaje. Sólo con el pago abierto."""
    liq = await db.get(Liquidacion, liquidacion_id)
    if not liq:
        raise HTTPException(404, "Liquidacion no encontrada")
    pago = await db.get(Pago, liq.pago_id)
    if pago and pago.estado != "A":
        raise HTTPException(409, "No se puede cambiar el porcentaje de una liquidación de un pago cerrado")
    medicos = await aplicar_porcentaje_pago(db, liq, payload.porcentaje_pago)
    await recalcular_totales_de_liquidacion(db, liq.id)
    await db.flush()
    await generar_y_recalcular_porcentuales(db, liq.pago_id)
    if pago is not None:
        for medico_id in medicos:
            await refrescar_detalle_medico(db, liq.pago_id, medico_id, pago=pago)
    await db.commit()
    await db.refresh(liq)
    return LiquidacionRead.model_validate(liq)


# ================================================
# GET: Lista liquidaciones por pago_id
# ================================================
@router.get("/liquidaciones_por_os/", response_model=List[LiquidacionRead])
async def listar_liquidaciones_por_pago(
    pago_id: int = Query(..., description="ID del pago"),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(Liquidacion, ObrasSociales.OBRA_SOCIAL)
        .outerjoin(ObrasSociales, ObrasSociales.NRO_OBRASOCIAL == Liquidacion.obra_social_id)
        .where(Liquidacion.pago_id == pago_id)
        .order_by(Liquidacion.obra_social_id)
    )
    return [
        LiquidacionRead.model_validate(liq).model_copy(update={"obra_social_nombre": nombre})
        for liq, nombre in res.all()
    ]


# ================================================
# GET: Obtiene una liquidación por su ID
# ================================================
@router.get("/liquidaciones_por_os/{liquidacion_id}", response_model=LiquidacionRead)
async def obtener_liquidacion(liquidacion_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Liquidacion).where(Liquidacion.id == liquidacion_id))
    obj = res.scalars().first()
    if not obj:
        raise HTTPException(404, "Liquidacion no encontrada")
    return obj


# ================================================
# DELETE: Elimina una liquidación
# ================================================
@router.delete("/liquidaciones_por_os/{liquidacion_id}", status_code=204)
async def eliminar_liquidacion(liquidacion_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Liquidacion).where(Liquidacion.id == liquidacion_id))
    obj = res.scalars().first()
    if not obj:
        raise HTTPException(404, "Liquidacion no encontrada")

    # 409 si pago cerrado
    pago = await db.get(Pago, obj.pago_id)
    if pago and pago.estado != "A":
        raise HTTPException(409, "No se puede eliminar una liquidación de un pago cerrado")

    # Un lote de esta factura que ya está en el pago NO impide quitarla, pero se va con ella: un lote con factura
    # sólo puede estar en un pago si su factura está en ese pago o ya fue pagada en otro. Más abajo, una vez
    # quitada la factura, los lotes que quedaron sin cobertura vuelven a «cerrado» y salen del pago.

    # Médicos con detalle en esta liquidación: sin esto su PagoMedico/Recibo
    # queda con montos viejos hasta que alguien vuelva a abrir su vista a mano
    # (ver diagnóstico M9). Se lee antes de borrar — después de eliminar la
    # liquidación estos médicos pueden no tener ya ningún DetalleLiquidacion
    # en el pago, y refrescar_todos_medicos (que arma la lista a partir de
    # DetalleLiquidacion existente) ya no los encontraría.
    medico_ids_afectados = list((await db.execute(
        select(DetalleLiquidacion.medico_id)
        .where(DetalleLiquidacion.liquidacion_id == liquidacion_id)
        .distinct()
    )).scalars().all())

    # Los ajustes de los lotes de esta factura no se tocan acá: cada uno lleva su propio porcentaje, y los que
    # salen del pago con su lote (más abajo) recuperan lo cargado.

    pago_id_del = obj.pago_id
    factura_id_del = obj.factura_id
    await db.delete(obj)
    await db.flush()
    if factura_id_del is not None:
        lotes_afuera = await lotes_sin_cobertura(db, pago_id_del, factura_id=factura_id_del)
        medico_ids_afectados = sorted(set(medico_ids_afectados) | set(await sacar_lotes_del_pago(db, lotes_afuera)))
    await generar_y_recalcular_porcentuales(db, pago_id_del)
    if pago is not None:
        for medico_id in medico_ids_afectados:
            await refrescar_detalle_medico(db, pago_id_del, medico_id, pago=pago)
    await db.commit()
    return None


# ================================================
# GET: Vista enriquecida de detalles
# ================================================
@router.get(
    "/liquidaciones_por_os/{liquidacion_id}/detalles_vista",
    response_model=list[DetalleVistaRow],
)
async def detalles_vista(
    liquidacion_id: int,
    medico_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None, description="Busca por NRO_SOCIO, NOMBRE o CODIGO_PRESTACION"),
    db: AsyncSession = Depends(get_db),
    response: Response = None,
):
    items, total = await vista_detalles_liquidacion(
        db=db,
        liquidacion_id=liquidacion_id,
        medico_id=medico_id,
        search=search,
    )

    if response is not None:
        response.headers["X-Total-Count"] = str(total)
        response.headers["Content-Range"] = f"items 0-{max(total-1,0)}/{total}"

    return items


# ================================================
# GET: Detalles brutos de una liquidación
# ================================================
@router.get(
    "/liquidaciones_por_os/{liquidacion_id}/detalles",
    response_model=List[DetalleLiquidacionRead],
)
async def listar_detalles_bruto_liquidacion(
    liquidacion_id: int,
    medico_id: Optional[int] = Query(None),
    obra_social_id: Optional[int] = Query(None),
    prestacion_id: Optional[int] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
):
    exists = await db.execute(select(Liquidacion.id).where(Liquidacion.id == liquidacion_id))
    if not exists.first():
        raise HTTPException(404, "Liquidación no encontrada")

    stmt = select(DetalleLiquidacion).where(DetalleLiquidacion.liquidacion_id == liquidacion_id)
    if medico_id is not None:
        stmt = stmt.where(DetalleLiquidacion.medico_id == medico_id)
    if obra_social_id is not None:
        stmt = stmt.where(DetalleLiquidacion.obra_social_id == obra_social_id)
    if prestacion_id is not None:
        stmt = stmt.where(DetalleLiquidacion.prestacion_id == prestacion_id)
    stmt = stmt.order_by(DetalleLiquidacion.id).offset(skip).limit(limit)

    res = await db.execute(stmt)
    return res.scalars().all()


# ================================================
# GET: Obtiene un recibo por ID
# ================================================
@router.get("/recibos/{recibo_id}", response_model=ReciboRead)
async def obtener_recibo(recibo_id: int, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(Recibo).where(Recibo.id == recibo_id))).scalars().first()
    if not row:
        raise HTTPException(404, "Recibo no encontrado")
    return row


# ================================================
# GET: Detalle completo de un recibo
# ================================================
@router.get("/recibos/{recibo_id}/detalle")
async def detalle_recibo_endpoint(recibo_id: int, db: AsyncSession = Depends(get_db)):
    recibo = (await db.execute(select(Recibo).where(Recibo.id == recibo_id))).scalars().first()
    if not recibo:
        raise HTTPException(404, "Recibo no encontrado")
    return await detalle_recibo_medico(db, recibo.pago_id, recibo.medico_id, recibo)

