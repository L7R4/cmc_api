from decimal import Decimal

from fastapi.responses import JSONResponse
from app.services.liquidaciones import _formatear_nro_factura, build_detalles_liquidacion, now_string, recalcular_totales_de_liquidacion, refacturar_service, recalcular_resumen_liquidacion, vista_detalles_liquidacion

from fastapi import APIRouter, Depends, HTTPException, Query, Path, Response
from pydantic import BaseModel, Field
from typing import Any, List, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
# from app.services.liquidaciones import generar_preview, normalizar_periodo_flexible
from sqlalchemy import select, update, delete, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from app.db.models import Debito_Credito, DetalleLiquidacion, LiquidacionResumen, Liquidacion, GuardarAtencion, Periodos
# from app.utils.main import normalizar_periodo
from app.schemas.liquidaciones_schema import (
    DetalleLiquidacionRead, DetalleVistaRow, LiquidacionResumenCreate, LiquidacionResumenRead, LiquidacionResumenWithItems,
    LiquidacionCreate, LiquidacionUpdate, LiquidacionRead, PreviewItem, PreviewResponse, RefacturarPayload,
)
import datetime as dt

router = APIRouter()

class GenerarReq(BaseModel):
    # JSON: {"obra_sociales_con_periodos": {"300":["2025-04","2025-07"], "412":["2025-06","2025-07"]}}
    obra_sociales_con_periodos: Dict[int, List[str]] = Field(
        ...,
        description="Mapa de obra social -> lista de periodos 'YYYY-MM'"
    )


# ================================================ 
# GET: Lista los periodos que ya tienen liquidación generada
# ================================================
@router.get("/resumen", response_model=List[LiquidacionResumenRead])
async def listar_resumenes(
    db: AsyncSession = Depends(get_db),
    mes: Optional[int] = Query(None, ge=1, le=12),
    anio: Optional[int] = Query(None, ge=1900, le=3000),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    stmt = select(LiquidacionResumen).order_by(LiquidacionResumen.anio.desc(), LiquidacionResumen.mes.desc())
    if mes is not None:
        stmt = stmt.where(LiquidacionResumen.mes == mes)
    if anio is not None:
        stmt = stmt.where(LiquidacionResumen.anio == anio)
    stmt = stmt.offset(skip).limit(limit)
    res = await db.execute(stmt)
    resumenes = res.scalars().all()

    # Recalcular los totales para cada resumen
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
# POST: Crea un resumen
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
    
    obj = LiquidacionResumen(
        mes=payload.mes,
        anio=payload.anio
    )
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    await db.flush()

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
# POST: Crea una liquidación por obra social
# ================================================
@router.post("/liquidaciones_por_os/crear", response_model=LiquidacionRead, status_code=201)
async def crear_liquidacion(payload: LiquidacionCreate, db: AsyncSession = Depends(get_db)):
    print("Crear liquidación con payload:", payload)
    exists_res = await db.execute(
        select(LiquidacionResumen.id).where(LiquidacionResumen.id == payload.resumen_id).limit(1)
    )
    if not exists_res.first():
        raise HTTPException(400, "resumen_id inválido")
    
    periodo_stmt = select(Periodos).where(Periodos.MES == payload.mes_periodo, Periodos.ANIO == payload.anio_periodo, Periodos.NRO_OBRA_SOCIAL == payload.obra_social_id, Periodos.CERRADO == "C").limit(1)
    res = await db.execute(periodo_stmt)
    obj = res.scalars().first()
    if not obj:
        raise HTTPException(400, "Periodo inválido")
    

    nro_factura = f"{obj.NRO_FACT_1}-{obj.NRO_FACT_2}"
    print("Nro factura generado:", nro_factura)
    # calcular versión y formatear nro_factura
    # nro_factura_formated = await _formatear_nro_factura(payload.punto_venta, payload.nro_factura)

    obj = Liquidacion(
        resumen_id=payload.resumen_id,
        obra_social_id=payload.obra_social_id,
        mes_periodo=payload.mes_periodo,
        anio_periodo=payload.anio_periodo,
        nro_factura=nro_factura,
        total_bruto=Decimal("0"),
        total_debitos=Decimal("0"),
        total_neto=Decimal("0"),
    )
    db.add(obj)
    await db.flush()  # para obtener obj.id

    # construir detalles + actualizar totales
    await build_detalles_liquidacion(db, obj.id)
    await recalcular_totales_de_liquidacion(db, obj.id)

    await db.commit()
    await db.refresh(obj)
    return obj


# ================================================
# GET: Obtiene una liquidación de la obra social por su ID
# ================================================
@router.get("/liquidaciones_por_os/{liquidacion_id}", response_model=LiquidacionRead)
async def obtener_liquidacion(liquidacion_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Liquidacion).where(Liquidacion.id == liquidacion_id))
    obj = res.scalars().first()
    if not obj:
        raise HTTPException(404, "Liquidacion no encontrada")
    return obj


# ================================================
# PUT: Obtiene una liquidación de la obra social por su ID
# ================================================
@router.put("/liquidaciones_por_os/{liquidacion_id}", response_model=LiquidacionRead)
async def editar_liquidacion(liquidacion_id: int, payload: LiquidacionUpdate, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Liquidacion).where(Liquidacion.id == liquidacion_id))
    obj = res.scalars().first()
    if not obj:
        raise HTTPException(404, "Liquidacion no encontrada")

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
# DELETE: Elimina una liquidación de la obra social por su ID
# ================================================
@router.delete("/liquidaciones_por_os/{liquidacion_id}", status_code=204)
async def eliminar_liquidacion(liquidacion_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Liquidacion).where(Liquidacion.id == liquidacion_id))
    obj = res.scalars().first()
    if not obj:
        raise HTTPException(404, "Liquidacion no encontrada")
    
    await db.delete(obj)
    await db.flush()
    await db.commit()
    return None



# ================================================
# GET: Obtener detalle vista liquidación por obra social con filtros
# ================================================
@router.get(
    "/liquidaciones_por_os/{liquidacion_id}/detalles_vista",
    response_model=list[DetalleVistaRow]
)
async def detalles_vista(
    liquidacion_id: int,
    medico_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None, description="Busca por NRO_SOCIO, NOMBRE (ListadoMedico) o CODIGO_PRESTACION"),
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
# GET: Obtener débitos y créditos aplicados segun filtros
# ================================================
@router.get("/debitos_creditos")
async def listar_debitos_creditos(
    medico_id: Optional[int] = None,
    obra_social_id: Optional[int] = None,
    periodo: Optional[str] = Query(None, description="YYYY-MM"),
    skip: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Debito_Credito)
    if medico_id is not None:
        # Si necesitás vincular por prestacion -> medico, esto se resuelve en una vista uniendo con DetalleLiquidacion.
        # Aquí filtro sólo por atributos nativos del DC.
        pass
    if obra_social_id is not None:
        stmt = stmt.where(Debito_Credito.obra_social_id == obra_social_id)
    if periodo:
        stmt = stmt.where(Debito_Credito.periodo == periodo)
    stmt = stmt.offset(skip).limit(limit)
    res = await db.execute(stmt)
    return [dc.__dict__ for dc in res.scalars().all()]


# ================================================
# POST: Cerrar una liquidación por obra social
# ================================================
@router.post("/liquidaciones_por_os/{liquidacion_id}/cerrar", status_code=204)
async def cerrar_liquidacion_endpoint(
    liquidacion_id: int,
    db: AsyncSession = Depends(get_db),
):
    liq = await db.get(Liquidacion, liquidacion_id)
    if not liq:
        raise HTTPException(404, "Liquidación no encontrada")
    if liq.estado == "C":
        raise HTTPException(409, "La liquidación ya está cerrada")

    # 1) calcular pagados detalle por detalle con la lógica nueva
    # await recomputar_todo_de_liquidacion(db, liquidacion_id)

    # 2) sellar estado
    liq.estado = "C"
    liq.cierre_timestamp = now_string()
    await db.commit()
    return None


# ================================================
# POST: Refacturar una liquidación por obra social pasandole nuevo punto de venta y nro de factura
# ================================================
@router.post("/liquidaciones_por_os/{liquidacion_id}/refacturar", response_model=LiquidacionRead, status_code=201)
async def refacturar(liquidacion_id: int, payload: RefacturarPayload, db: AsyncSession = Depends(get_db)):
    nueva = await refacturar_service(db, liquidacion_id, payload.punto_venta, payload.nro_factura)
    await db.commit()
    # await db.refresh(nueva)
    return JSONResponse({
        "id": nueva.id,
        "resumen_id": nueva.resumen_id,
        "mes_periodo": nueva.mes_periodo,
        "anio_periodo": nueva.anio_periodo,
        "estado": nueva.estado,
        "nro_factura": nueva.nro_factura,
        "total_bruto": nueva.total_bruto,
        "total_debitos": nueva.total_debitos,
        "total_neto": nueva.total_neto,
    })



# ---------- PRESTACIONES (RAW) ya persistidas en DetalleLiquidacion ----------
@router.get(
    "/liquidaciones_por_os/{liquidacion_id}/detalles",
    response_model=List[DetalleLiquidacionRead],
)
async def listar_detalles_bruto_liquidacion(
    liquidacion_id: int,
    medico_id: Optional[int] = Query(None),
    obra_social_id: Optional[int] = Query(None),
    prestacion_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
):
    # validar que existe la liquidación
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


@router.post("/liquidaciones_por_os/{liquidacion_id}/reabrir", response_model=LiquidacionRead, status_code=200)
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
    # devolvemos lo básico que consume el front
    return JSONResponse({
        "id": liq.id,
        "resumen_id": liq.resumen_id,
        "mes_periodo": liq.mes_periodo,
        "anio_periodo": liq.anio_periodo,
        "estado": liq.estado,
        "nro_factura": liq.nro_factura,
        "total_bruto": str(liq.total_bruto or 0),
        "total_debitos": str(liq.total_debitos or 0),
        "total_neto": str(liq.total_neto or 0),
    })

