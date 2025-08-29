from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query, Path
from pydantic import BaseModel, Field
from typing import Any, List, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.services.liquidaciones import generar_preview
from sqlalchemy import select, update, delete, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from app.db.models import LiquidacionResumen, Liquidacion, GuardarAtencion
from app.utils.main import normalizar_periodo
from app.schemas.liquidaciones_schema import (
    LiquidacionResumenCreate, LiquidacionResumenUpdate, LiquidacionResumenRead, LiquidacionResumenWithItems,
    LiquidacionCreate, LiquidacionUpdate, LiquidacionRead,
)

router = APIRouter()

class GenerarReq(BaseModel):
    # JSON: {"obra_sociales_con_periodos": {"300":["2025-04","2025-07"], "412":["2025-06","2025-07"]}}
    obra_sociales_con_periodos: Dict[int, List[str]] = Field(
        ...,
        description="Mapa de obra social -> lista de periodos 'YYYY-MM'"
    )
@router.post("/generar")
async def generar(req: GenerarReq, db: AsyncSession = Depends(get_db)) -> Any:
    salida = await generar_preview(db, req.obra_sociales_con_periodos)
    if salida.get("status") != "ok":
        raise HTTPException(400, salida.get("message", "error"))
    return salida



@router.get("/resumen", response_model=List[LiquidacionResumenRead])
async def listar_resumenes(
    db: AsyncSession = Depends(get_db),
    mes: Optional[int] = Query(None, ge=1, le=12),
    anio: Optional[int] = Query(None, ge=1900, le=3000),
    estado: Optional[str] = Query(None, pattern="^(a|c|e)$"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    stmt = select(LiquidacionResumen).order_by(LiquidacionResumen.anio.desc(), LiquidacionResumen.mes.desc())
    if mes is not None:
        stmt = stmt.where(LiquidacionResumen.mes == mes)
    if anio is not None:
        stmt = stmt.where(LiquidacionResumen.anio == anio)
    if estado is not None:
        stmt = stmt.where(LiquidacionResumen.estado == estado)
    stmt = stmt.offset(skip).limit(limit)
    res = await db.execute(stmt)
    return res.scalars().all()

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
    return obj

@router.post("/resumen", response_model=LiquidacionResumenRead, status_code=201)
async def crear_resumen(payload: LiquidacionResumenCreate, db: AsyncSession = Depends(get_db)):
    obj = LiquidacionResumen(
        mes=payload.mes,
        anio=payload.anio,
        estado=payload.estado.value if hasattr(payload.estado, "value") else payload.estado,
        cierre_timestamp=payload.cierre_timestamp,
        total_bruto=Decimal("0"),
        total_debitos=Decimal("0"),
        total_deduccion=Decimal("0"),
    )
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj

@router.put("/resumen/{resumen_id}", response_model=LiquidacionResumenRead)
async def editar_resumen(resumen_id: int, payload: LiquidacionResumenUpdate, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(LiquidacionResumen).where(LiquidacionResumen.id == resumen_id))
    obj = res.scalars().first()
    if not obj:
        raise HTTPException(404, "LiquidacionResumen no encontrado")

    # actualizar campos permitidos
    if payload.mes is not None: obj.mes = payload.mes
    if payload.anio is not None: obj.anio = payload.anio
    if payload.nros_liquidacion is not None: obj.nros_liquidacion = payload.nros_liquidacion
    if payload.estado is not None: obj.estado = payload.estado.value if hasattr(payload.estado, "value") else payload.estado
    if payload.cierre_timestamp is not None: obj.cierre_timestamp = payload.cierre_timestamp

    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(409, f"Conflicto al actualizar: {e.orig}")  # p.ej. si tenés algún constraint nuevo
    await db.refresh(obj)
    return obj

@router.delete("/resumen/{resumen_id}", status_code=204)
async def eliminar_resumen(resumen_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(LiquidacionResumen).where(LiquidacionResumen.id == resumen_id))
    obj = res.scalars().first()
    if not obj:
        raise HTTPException(404, "LiquidacionResumen no encontrado")
    await db.delete(obj)  # gracias a ondelete="CASCADE" + cascade ORM borra hijas
    await db.commit()
    return None



# ========================
# Liquidacion CRUD
# ========================

@router.get("/liquidaciones_por_os", response_model=List[LiquidacionRead])
async def listar_liquidaciones(
    db: AsyncSession = Depends(get_db),
    resumen_id: Optional[int] = None,
    obra_social_id: Optional[int] = None,
    mes_periodo: Optional[int] = None,
    anio_periodo: Optional[int] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    stmt = select(Liquidacion)
    if resumen_id is not None:
        stmt = stmt.where(Liquidacion.resumen_id == resumen_id)
    if obra_social_id is not None:
        stmt = stmt.where(Liquidacion.obra_social_id == obra_social_id)
    if mes_periodo is not None:
        stmt = stmt.where(Liquidacion.mes_periodo == mes_periodo)
    if anio_periodo is not None:
        stmt = stmt.where(Liquidacion.anio_periodo == anio_periodo)
    stmt = stmt.order_by(Liquidacion.id.desc()).offset(skip).limit(limit)

    res = await db.execute(stmt)
    return res.scalars().all()

@router.get("/liquidaciones_por_os/{liquidacion_id}", response_model=LiquidacionRead)
async def obtener_liquidacion(liquidacion_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Liquidacion).where(Liquidacion.id == liquidacion_id))
    obj = res.scalars().first()
    if not obj:
        raise HTTPException(404, "Liquidacion no encontrada")
    return obj


@router.get("/liquidaciones_por_os/{obra_social_id}/{periodo_id}")
async def prestaciones_por_os_y_periodo(
    obra_social_id: int = Path(..., description="Código de obra social"),
    periodo_id: str = Path(..., description="YYYY-MM o YYYYMM"),
    limit: int = Query(5000, ge=1, le=20000),
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    try:
        anio, mes, _ = normalizar_periodo(periodo_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    stmt = (
        select(
            GuardarAtencion.ID.label("id_atencion"),
            GuardarAtencion.NRO_SOCIO.label("medico_id"),
            GuardarAtencion.NOMBRE_PRESTADOR.label("medico_nombre"),
            GuardarAtencion.NRO_OBRA_SOCIAL.label("obra_social_id"),
            GuardarAtencion.CODIGO_PRESTACION.label("codigo_prestacion"),
            GuardarAtencion.FECHA_PRESTACION.label("fecha_prestacion"),
            GuardarAtencion.VALOR_CIRUJIA.label("valor_cirugia"),
            GuardarAtencion.VALOR_AYUDANTE.label("valor_ayudante"),
            GuardarAtencion.VALOR_AYUDANTE_2.label("valor_ayudante_2"),
            GuardarAtencion.GASTOS.label("gastos"),
            GuardarAtencion.CANTIDAD.label("cantidad"),
            GuardarAtencion.CANT_TRATAMIENTO.label("cantidad_tratamiento"),
        )
        .where(
            and_(
                GuardarAtencion.NRO_OBRA_SOCIAL == obra_social_id,
                GuardarAtencion.ANIO_PERIODO == anio,
                GuardarAtencion.MES_PERIODO == mes,
            )
        )
        .limit(limit)
    )
    rows = (await db.execute(stmt)).mappings().all()
    return [dict(r) for r in rows]


@router.post("/liquidaciones_por_os/crear", response_model=LiquidacionRead, status_code=201)
async def crear_liquidacion(payload: LiquidacionCreate, db: AsyncSession = Depends(get_db)):
    # opcional: validar existencia de resumen_id antes
    exists_res = await db.execute(
        select(LiquidacionResumen.id).where(LiquidacionResumen.id == payload.resumen_id).limit(1)
    )
    if not exists_res.first():
        raise HTTPException(400, "resumen_id inválido")

    obj = Liquidacion(
        resumen_id=payload.resumen_id,
        obra_social_id=payload.obra_social_id,
        mes_periodo=payload.mes_periodo,
        anio_periodo=payload.anio_periodo,
        nro_liquidacion=payload.nro_liquidacion,
        total_bruto=Decimal("0"),
        total_debitos=Decimal("0"),
        total_neto=Decimal("0"),
    )
    db.add(obj)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        # típico: viola uq_liq_res_os_per (ya existe esa OS+periodo dentro del mismo resumen)
        raise HTTPException(409, f"No se pudo crear la liquidación: {e.orig}")
    await db.refresh(obj)
    return obj

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
    if payload.nro_liquidacion is not None:
        obj.nro_liquidacion = payload.nro_liquidacion

    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(409, f"Conflicto de unicidad u otro constraint: {e.orig}")
    await db.refresh(obj)
    return obj

@router.delete("/liquidaciones_por_os/{liquidacion_id}", status_code=204)
async def eliminar_liquidacion(liquidacion_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Liquidacion).where(Liquidacion.id == liquidacion_id))
    obj = res.scalars().first()
    if not obj:
        raise HTTPException(404, "Liquidacion no encontrada")
    await db.delete(obj)
    await db.commit()
    return None