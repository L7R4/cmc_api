import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.database import get_db
from app.db.models import (
    Debito_Credito,
    DetalleLiquidacion,
    GuardarAtencion,
    Liquidacion,
    LiquidacionMedico,
    LiquidacionResumen,
    Periodos,
    Recibo,
    ReciboItem,
)
from app.modules.liquidacion.schemas import (
    DetalleLiquidacionRead,
    DetalleVistaRow,
    LiquidacionCreate,
    LiquidacionMedicoRead,
    LiquidacionMedicoResumen,
    LiquidacionRead,
    LiquidacionResumenCreate,
    LiquidacionResumenRead,
    LiquidacionResumenWithItems,
    LiquidacionUpdate,
    PreviewItem,
    PreviewResponse,
    ReciboAnularPayload,
    ReciboRead,
    RefacturarPayload,
)
from app.modules.liquidacion.service import (
    _formatear_nro_factura,
    build_detalles_liquidacion,
    emitir_recibos,
    generar_liquidacion_medico,
    recalcular_resumen_liquidacion,
    recalcular_totales_de_liquidacion,
    refacturar_service,
    vista_detalles_liquidacion,
)

router = APIRouter()


class GenerarReq(BaseModel):
    obra_sociales_con_periodos: Dict[int, List[str]] = Field(
        ...,
        description="Mapa de obra social -> lista de periodos 'YYYY-MM'",
    )


# ================================================
# GET: Lista resumenes de liquidación
# ================================================
@router.get("/resumen", response_model=List[LiquidacionResumenRead])
async def listar_resumenes(
    db: AsyncSession = Depends(get_db),
    mes: Optional[int] = Query(None, ge=1, le=12),
    anio: Optional[int] = Query(None, ge=1900, le=3000),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    stmt = select(LiquidacionResumen).order_by(
        LiquidacionResumen.anio.desc(), LiquidacionResumen.mes.desc()
    )
    if mes is not None:
        stmt = stmt.where(LiquidacionResumen.mes == mes)
    if anio is not None:
        stmt = stmt.where(LiquidacionResumen.anio == anio)
    stmt = stmt.offset(skip).limit(limit)
    res = await db.execute(stmt)
    resumenes = res.scalars().all()

    resultados = []
    for resumen in resumenes:
        totales = await recalcular_resumen_liquidacion(db, resumen.id)
        resumen.total_bruto = totales["total_bruto"]
        resumen.total_debitos = totales["total_debitos"]
        resumen.total_deduccion = totales["total_deduccion"]
        resumen.total_neto = totales["total_neto"]
        resultados.append(resumen)

    return resultados


# ================================================
# POST: Crea un resumen global
# ================================================
@router.post("/resumen", response_model=LiquidacionResumenRead, status_code=201)
async def crear_resumen(payload: LiquidacionResumenCreate, db: AsyncSession = Depends(get_db)):
    exists = await db.execute(
        select(LiquidacionResumen.id)
        .where(LiquidacionResumen.anio == payload.anio, LiquidacionResumen.mes == payload.mes)
        .limit(1)
    )
    existing_id = exists.scalars().first()
    if existing_id:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "exists",
                "resumen_id": existing_id,
                "message": f"Ya existe un resumen para {payload.anio}-{payload.mes:02d}",
            },
        )

    obj = LiquidacionResumen(mes=payload.mes, anio=payload.anio)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)

    totales = await recalcular_resumen_liquidacion(db, obj.id)
    obj.total_bruto = totales["total_bruto"]
    obj.total_debitos = totales["total_debitos"]
    obj.total_deduccion = totales["total_deduccion"]
    obj.total_neto = totales["total_neto"]
    return obj


# ================================================
# DELETE: Elimina un resumen
# ================================================
@router.delete("/resumen/{resumen_id}", status_code=204)
async def eliminar_resumen(resumen_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(LiquidacionResumen).where(LiquidacionResumen.id == resumen_id))
    obj = res.scalars().first()
    if not obj:
        raise HTTPException(404, "LiquidacionResumen no encontrado")
    await db.delete(obj)
    await db.commit()
    return None


# ================================================
# GET: Obtiene un resumen por su ID
# ================================================
@router.get("/resumen/{resumen_id}", response_model=LiquidacionResumenWithItems)
async def obtener_resumen(resumen_id: int, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(LiquidacionResumen)
        .options(selectinload(LiquidacionResumen.liquidaciones))
        .where(LiquidacionResumen.id == resumen_id)
        .limit(1)
    )
    res = await db.execute(stmt)
    obj = res.scalars().first()
    if not obj:
        raise HTTPException(404, "LiquidacionResumen no encontrado")

    totales = await recalcular_resumen_liquidacion(db, resumen_id)
    obj.total_bruto = totales["total_bruto"]
    obj.total_debitos = totales["total_debitos"]
    obj.total_deduccion = totales["total_deduccion"]
    obj.total_neto = totales["total_neto"]

    return obj


# ================================================
# POST: Genera LiquidacionMedico para un resumen (AGENT DO IT)
# ================================================
@router.post("/resumen/{resumen_id}/generar_liquidacion_medico", status_code=200)
async def generar_liquidacion_medico_endpoint(
    resumen_id: int, db: AsyncSession = Depends(get_db)
):
    """
    Calcula y persiste el resumen por médico para el resumen indicado.
    Incluye: bruto, débitos OS, créditos OS, reconocido, deducciones internas, neto.
    Es idempotente: si ya existe, actualiza.
    """
    items = await generar_liquidacion_medico(db, resumen_id)
    await db.commit()
    return {"resumen_id": resumen_id, "total_medicos": len(items), "items": items}


# ================================================
# GET: Lista LiquidacionMedico de un resumen
# ================================================
@router.get("/resumen/{resumen_id}/liquidacion_medico", response_model=List[LiquidacionMedicoRead])
async def listar_liquidacion_medico(
    resumen_id: int,
    db: AsyncSession = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
):
    stmt = (
        select(LiquidacionMedico)
        .where(LiquidacionMedico.resumen_id == resumen_id)
        .order_by(LiquidacionMedico.medico_id)
        .offset(skip)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return rows


# ================================================
# GET: Obtiene LiquidacionMedico de un médico en un resumen
# ================================================
@router.get(
    "/resumen/{resumen_id}/liquidacion_medico/{medico_id}",
    response_model=LiquidacionMedicoRead,
)
async def obtener_liquidacion_medico(
    resumen_id: int, medico_id: int, db: AsyncSession = Depends(get_db)
):
    row = (await db.execute(
        select(LiquidacionMedico).where(
            LiquidacionMedico.resumen_id == resumen_id,
            LiquidacionMedico.medico_id == medico_id,
        )
    )).scalars().first()
    if not row:
        raise HTTPException(404, "No existe liquidacion_medico para ese médico en ese resumen")
    return row


# ================================================
# POST: Emitir recibos para un resumen (AGENT DO IT)
# ================================================
@router.post("/resumen/{resumen_id}/emitir_recibos", status_code=200)
async def emitir_recibos_endpoint(
    resumen_id: int, db: AsyncSession = Depends(get_db)
):
    """
    Genera recibos para todos los médicos con LiquidacionMedico en el resumen.
    Requiere:
    - Al menos una liquidación cerrada en el resumen.
    - Haber ejecutado generar_liquidacion_medico antes.
    Es idempotente: si ya existe recibo emitido, lo actualiza solo si estaba anulado.
    """
    items = await emitir_recibos(db, resumen_id)
    await db.commit()
    return {"resumen_id": resumen_id, "total_recibos": len(items), "recibos": items}


# ================================================
# GET: Lista recibos de un resumen
# ================================================
@router.get("/resumen/{resumen_id}/recibos", response_model=List[ReciboRead])
async def listar_recibos(
    resumen_id: int,
    db: AsyncSession = Depends(get_db),
    estado: Optional[str] = Query(None, description="Filtrar por estado: emitido|anulado|pagado"),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
):
    stmt = (
        select(Recibo)
        .options(selectinload(Recibo.items))
        .where(Recibo.resumen_id == resumen_id)
    )
    if estado:
        stmt = stmt.where(Recibo.estado == estado)
    stmt = stmt.order_by(Recibo.medico_id).offset(skip).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return rows


# ================================================
# GET: Obtiene un recibo por ID
# ================================================
@router.get("/recibos/{recibo_id}", response_model=ReciboRead)
async def obtener_recibo(recibo_id: int, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Recibo)
        .options(selectinload(Recibo.items))
        .where(Recibo.id == recibo_id)
    )
    row = (await db.execute(stmt)).scalars().first()
    if not row:
        raise HTTPException(404, "Recibo no encontrado")
    return row


# ================================================
# PUT: Anular un recibo
# ================================================
@router.put("/recibos/{recibo_id}/anular", response_model=ReciboRead)
async def anular_recibo(
    recibo_id: int,
    payload: ReciboAnularPayload,
    db: AsyncSession = Depends(get_db),
):
    """Solo se puede anular un recibo en estado 'emitido'."""
    stmt = select(Recibo).options(selectinload(Recibo.items)).where(Recibo.id == recibo_id)
    recibo = (await db.execute(stmt)).scalars().first()
    if not recibo:
        raise HTTPException(404, "Recibo no encontrado")
    if recibo.estado == "anulado":
        raise HTTPException(409, "El recibo ya está anulado")
    if recibo.estado == "pagado":
        raise HTTPException(409, "No se puede anular un recibo ya pagado")

    recibo.estado = "anulado"
    await db.commit()
    await db.refresh(recibo)
    return recibo


# ================================================
# POST: Crea una liquidación por obra social
# ================================================
@router.post("/liquidaciones_por_os/crear", response_model=LiquidacionRead, status_code=201)
async def crear_liquidacion(payload: LiquidacionCreate, db: AsyncSession = Depends(get_db)):
    exists_res = await db.execute(
        select(LiquidacionResumen.id).where(LiquidacionResumen.id == payload.resumen_id).limit(1)
    )
    if not exists_res.first():
        raise HTTPException(400, "resumen_id inválido")

    periodo_stmt = select(Periodos).where(
        Periodos.MES == payload.mes_periodo,
        Periodos.ANIO == payload.anio_periodo,
        Periodos.NRO_OBRA_SOCIAL == payload.obra_social_id,
        Periodos.CERRADO == "C",
    ).limit(1)
    res = await db.execute(periodo_stmt)
    periodo_obj = res.scalars().first()
    if not periodo_obj:
        raise HTTPException(400, "Periodo inválido o no cerrado")

    nro_factura = f"{periodo_obj.NRO_FACT_1}-{periodo_obj.NRO_FACT_2}"

    liq = Liquidacion(
        resumen_id=payload.resumen_id,
        obra_social_id=payload.obra_social_id,
        mes_periodo=payload.mes_periodo,
        anio_periodo=payload.anio_periodo,
        nro_factura=nro_factura,
        total_bruto=Decimal("0"),
        total_debitos=Decimal("0"),
        total_neto=Decimal("0"),
    )
    db.add(liq)
    await db.flush()

    await build_detalles_liquidacion(db, liq.id)
    await recalcular_totales_de_liquidacion(db, liq.id)

    await db.commit()
    await db.refresh(liq)
    return liq


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
# PUT: Actualiza una liquidación
# ================================================
@router.put("/liquidaciones_por_os/{liquidacion_id}", response_model=LiquidacionRead)
async def editar_liquidacion(
    liquidacion_id: int, payload: LiquidacionUpdate, db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(Liquidacion).where(Liquidacion.id == liquidacion_id))
    obj = res.scalars().first()
    if not obj:
        raise HTTPException(404, "Liquidacion no encontrada")
    if obj.estado == "C":
        raise HTTPException(409, "No se puede editar una liquidación cerrada")

    if payload.obra_social_id is not None:
        obj.obra_social_id = payload.obra_social_id
    if payload.mes_periodo is not None:
        obj.mes_periodo = payload.mes_periodo
    if payload.anio_periodo is not None:
        obj.anio_periodo = payload.anio_periodo
    if payload.nro_factura is not None:
        obj.nro_factura = payload.nro_factura

    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(409, f"Conflicto de unicidad u otro constraint: {e.orig}")
    await db.refresh(obj)
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

    await db.delete(obj)
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
# GET: Listar débitos/créditos
# ================================================
@router.get("/debitos_creditos")
async def listar_debitos_creditos(
    obra_social_id: Optional[int] = None,
    anio: Optional[int] = Query(None, description="Año del período"),
    mes: Optional[int] = Query(None, ge=1, le=12, description="Mes del período"),
    skip: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
):
    """Lista DCs filtrado por obra_social y/o año+mes (antes se filtraba por 'periodo' string)."""
    stmt = select(Debito_Credito)
    if obra_social_id is not None:
        stmt = stmt.where(Debito_Credito.obra_social_id == obra_social_id)
    if anio is not None:
        stmt = stmt.where(Debito_Credito.anio == anio)
    if mes is not None:
        stmt = stmt.where(Debito_Credito.mes == mes)
    stmt = stmt.offset(skip).limit(limit)
    res = await db.execute(stmt)
    return [dc.__dict__ for dc in res.scalars().all()]


# ================================================
# POST: Cerrar una liquidación
# ================================================
@router.post("/liquidaciones_por_os/{liquidacion_id}/cerrar", status_code=204)
async def cerrar_liquidacion_endpoint(liquidacion_id: int, db: AsyncSession = Depends(get_db)):
    liq = await db.get(Liquidacion, liquidacion_id)
    if not liq:
        raise HTTPException(404, "Liquidación no encontrada")
    if liq.estado == "C":
        raise HTTPException(409, "La liquidación ya está cerrada")

    liq.estado = "C"
    # AGENT DO IT: cierre_timestamp como datetime real
    liq.cierre_timestamp = datetime.datetime.now()
    await db.commit()
    return None


# ================================================
# POST: Refacturar una liquidación
# ================================================
@router.post(
    "/liquidaciones_por_os/{liquidacion_id}/refacturar",
    response_model=LiquidacionRead,
    status_code=201,
)
async def refacturar(
    liquidacion_id: int, payload: RefacturarPayload, db: AsyncSession = Depends(get_db)
):
    nueva = await refacturar_service(db, liquidacion_id, payload.punto_venta, payload.nro_factura)
    await db.commit()
    await db.refresh(nueva)
    return nueva


# ================================================
# POST: Reabrir una liquidación cerrada
# ================================================
@router.post(
    "/liquidaciones_por_os/{liquidacion_id}/reabrir",
    response_model=LiquidacionRead,
    status_code=200,
)
async def reabrir_simple_endpoint(liquidacion_id: int, db: AsyncSession = Depends(get_db)):
    liq = await db.get(Liquidacion, liquidacion_id)
    if not liq:
        raise HTTPException(404, "Liquidación no encontrada")
    if liq.estado != "C":
        raise HTTPException(409, "Solo se puede reabrir una liquidación cerrada")

    liq.estado = "A"
    liq.cierre_timestamp = None
    await db.commit()
    await db.refresh(liq)
    return liq
