from decimal import Decimal

from fastapi.responses import JSONResponse
from app.services.liquidaciones import now_string, reabrir_liquidacion_creando_version, reabrir_liquidacion_simple, recomputar_todo_de_liquidacion, recomputar_totales_de_liquidacion, recomputar_totales_de_resumen
from app.services.liquidaciones_calc import (
    calcular_version_y_formatear_nro,
    construir_detalles_y_totales,
    vista_detalles_liquidacion
    )

from fastapi import APIRouter, Depends, HTTPException, Query, Path, Response
from pydantic import BaseModel, Field
from typing import Any, List, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
# from app.services.liquidaciones import generar_preview, normalizar_periodo_flexible
from sqlalchemy import select, update, delete, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from app.db.models import Debito_Credito, DetalleLiquidacion, LiquidacionResumen, Liquidacion, GuardarAtencion
# from app.utils.main import normalizar_periodo
from app.schemas.liquidaciones_schema import (
    DetalleLiquidacionRead, DetalleVistaRow, LiquidacionResumenCreate, LiquidacionResumenUpdate, LiquidacionResumenRead, LiquidacionResumenWithItems,
    LiquidacionCreate, LiquidacionUpdate, LiquidacionRead, PreviewItem, PreviewResponse, RefacturarPayload,
)



router = APIRouter()

class GenerarReq(BaseModel):
    # JSON: {"obra_sociales_con_periodos": {"300":["2025-04","2025-07"], "412":["2025-06","2025-07"]}}
    obra_sociales_con_periodos: Dict[int, List[str]] = Field(
        ...,
        description="Mapa de obra social -> lista de periodos 'YYYY-MM'"
    )
# @router.post("/generar")
# async def generar(req: GenerarReq, db: AsyncSession = Depends(get_db)) -> Any:
#     salida = await generar_preview(db, req.obra_sociales_con_periodos)
#     if salida.get("status") != "ok":
#         raise HTTPException(400, salida.get("message", "error"))
#     return salida



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

@router.get("/resumen/{resumen_id}/preview", response_model=PreviewResponse)
async def preview_liquidaciones(resumen_id: int, db: AsyncSession = Depends(get_db)):
    # Traemos TODAS las liquidaciones del resumen
    liqs = (await db.execute(
        select(Liquidacion).where(Liquidacion.resumen_id == resumen_id)
    )).scalars().all()

    if not liqs:
        # Si no hay, devolvemos todo en 0
        z = Decimal("0")
        return {
            "items": [],
            "totals": {
                "cerradas_bruto": z, "cerradas_debitos": z, "cerradas_neto": z,
                "abiertas_bruto": z, "abiertas_debitos": z, "abiertas_neto": z,
                "resumen_deduccion": z, "total_general": z
            }
        }

    items: List[PreviewItem] = []
    # Podrías mapear nombres de OS aquí si tienes el modelo ObraSocial.
    # Para mantenerlo genérico, dejamos 'obra_social_nombre=None'
    for liq in liqs:
        y = int(liq.anio_periodo)
        m = int(liq.mes_periodo)
        periodo = f"{y:04d}-{m:02d}"
        estado = (liq.estado or "A").upper()
        bruto = Decimal(str(liq.total_bruto or 0))
        debitos = Decimal(str(liq.total_debitos or 0))
        deduccion = Decimal(str(getattr(liq, "total_deduccion", 0) or 0))
        neto = Decimal(str(liq.total_neto or (bruto - (debitos + deduccion))))

        items.append({
            "liquidacion_id": liq.id,
            "obra_social_id": int(liq.obra_social_id),
            "obra_social_nombre": None,  # <- si tienes el nombre aquí, colócalo
            "periodo": periodo,
            "estado": "C" if estado == "C" else "A",
            "nro_liquidacion": liq.nro_liquidacion,
            "total_bruto": bruto,
            "total_debitos": debitos,
            "total_deduccion": deduccion,
            "total_neto": neto,
        })

    from decimal import Decimal
    z = Decimal("0")
    c_bruto = sum((it["total_bruto"] for it in items if it["estado"] == "C"), z)
    c_deb = sum((it["total_debitos"] for it in items if it["estado"] == "C"), z)
    c_neto = sum((it["total_neto"] for it in items if it["estado"] == "C"), z)
    a_bruto = sum((it["total_bruto"] for it in items if it["estado"] == "A"), z)
    a_deb = sum((it["total_debitos"] for it in items if it["estado"] == "A"), z)
    a_neto = sum((it["total_neto"] for it in items if it["estado"] == "A"), z)

    # Para traer la deducción del resumen, puedes hacer un SELECT del modelo Resumen si la guardas ahí.
    # Si no, deja 0 y el front mostrará la que ya tiene.
    resumen_deduccion = z
    total_general = c_neto + a_neto + resumen_deduccion

    return {
        "items": items,
        "totals": {
            "cerradas_bruto": c_bruto, "cerradas_debitos": c_deb, "cerradas_neto": c_neto,
            "abiertas_bruto": a_bruto, "abiertas_debitos": a_deb, "abiertas_neto": a_neto,
            "resumen_deduccion": resumen_deduccion,
            "total_general": total_general
        }
    }


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


# @router.get("/liquidaciones_por_os/{obra_social_id}/{periodo_id}")
# async def prestaciones_por_os_y_periodo(
#     obra_social_id: int = Path(..., description="Código de obra social"),
#     periodo_id: str = Path(..., description="YYYY-MM o YYYYMM"),
#     limit: int = Query(5000, ge=1, le=20000),
#     db: AsyncSession = Depends(get_db),
# ) -> List[Dict[str, Any]]:
#     try:
#         anio, mes, _ = normalizar_periodo_flexible(periodo_id)
#     except ValueError as e:
#         raise HTTPException(400, str(e))

#     stmt = (
#         select(
#             GuardarAtencion.ID.label("id_atencion"),
#             GuardarAtencion.NRO_SOCIO.label("medico_id"),
#             GuardarAtencion.NOMBRE_PRESTADOR.label("medico_nombre"),
#             GuardarAtencion.NRO_OBRA_SOCIAL.label("obra_social_id"),
#             GuardarAtencion.CODIGO_PRESTACION.label("codigo_prestacion"),
#             GuardarAtencion.FECHA_PRESTACION.label("fecha_prestacion"),
#             GuardarAtencion.VALOR_CIRUJIA.label("valor_cirugia"),
#             GuardarAtencion.VALOR_AYUDANTE.label("valor_ayudante"),
#             GuardarAtencion.VALOR_AYUDANTE_2.label("valor_ayudante_2"),
#             GuardarAtencion.GASTOS.label("gastos"),
#             GuardarAtencion.CANTIDAD.label("cantidad"),
#             GuardarAtencion.CANT_TRATAMIENTO.label("cantidad_tratamiento"),
#         )
#         .where(
#             and_(
#                 GuardarAtencion.NRO_OBRA_SOCIAL == obra_social_id,
#                 GuardarAtencion.ANIO_PERIODO == anio,
#                 GuardarAtencion.MES_PERIODO == mes,
#             )
#         )
#         .limit(limit)
#     )
#     rows = (await db.execute(stmt)).mappings().all()
#     return [dict(r) for r in rows]


@router.post("/liquidaciones_por_os/crear", response_model=LiquidacionRead, status_code=201)
async def crear_liquidacion(payload: LiquidacionCreate, db: AsyncSession = Depends(get_db)):
    # validar resumen
    exists_res = await db.execute(
        select(LiquidacionResumen.id).where(LiquidacionResumen.id == payload.resumen_id).limit(1)
    )
    if not exists_res.first():
        raise HTTPException(400, "resumen_id inválido")

    # calcular versión y formatear nro_liquidacion
    version, nro_fmt = await calcular_version_y_formatear_nro(
        db, payload.obra_social_id, payload.anio_periodo, payload.mes_periodo, payload.nro_liquidacion
    )

    obj = Liquidacion(
        resumen_id=payload.resumen_id,
        obra_social_id=payload.obra_social_id,
        mes_periodo=payload.mes_periodo,
        anio_periodo=payload.anio_periodo,
        version=version,
        nro_liquidacion=nro_fmt,
        total_bruto=Decimal("0"),
        total_debitos=Decimal("0"),
        total_neto=Decimal("0"),
    )
    db.add(obj)
    await db.flush()  # para obtener obj.id

    # construir detalles + actualizar totales
    await construir_detalles_y_totales(db, obj.id)
    await recomputar_totales_de_liquidacion(db, obj.id)
    await recomputar_totales_de_resumen(db,obj.resumen_id)

    await db.commit()
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
    await db.flush()
    await recomputar_totales_de_resumen(db,obj.resumen_id)
    await db.commit()
    return None


# ---------- PRESTACIONES (RAW) ya persistidas en DetalleLiquidacion ----------
@router.get(
    "/liquidaciones_por_os/{liquidacion_id}/detalles",
    response_model=List[DetalleLiquidacionRead],
)
async def listar_detalles_liquidacion(
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

# Mantén el alias actual si ya lo usas en el front
@router.get(
    "/liquidaciones_por_os/{liquidacion_id}/detalles_vista",
    response_model=List[DetalleVistaRow]
)
async def detalles_vista(
    liquidacion_id: int,
    medico_id: Optional[int] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    response: Response = None,
):
    items, total = await vista_detalles_liquidacion(
        db=db,
        liquidacion_id=liquidacion_id,
        medico_id=medico_id,
        offset=offset,
        limit=limit,
    )

    # Headers útiles para el front
    response.headers["X-Total-Count"] = str(total)
    end = offset + max(len(items) - 1, 0)
    response.headers["Content-Range"] = f"items {offset}-{end}/{total}"
    response.headers["X-Offset"] = str(offset)
    response.headers["X-Limit"] = str(limit)

    return items

# ---- Débitos/Créditos listado con filtros ----
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
    await recomputar_todo_de_liquidacion(db, liquidacion_id)

    # 2) sellar estado
    liq.estado = "C"
    liq.cierre_timestamp = now_string()
    await db.commit()
    return None

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
        "nro_liquidacion": liq.nro_liquidacion,
        "total_bruto": str(liq.total_bruto or 0),
        "total_debitos": str(liq.total_debitos or 0),
        "total_neto": str(liq.total_neto or 0),
    })


@router.post("/liquidaciones_por_os/{liquidacion_id}/refacturar", response_model=LiquidacionRead, status_code=201)
async def refacturar_endpoint(liquidacion_id: int, payload: RefacturarPayload, db: AsyncSession = Depends(get_db)):
    nueva = await reabrir_liquidacion_creando_version(db, liquidacion_id, payload.nro_liquidacion)
    await db.commit()
    await db.refresh(nueva)
    return JSONResponse({
        "id": nueva.id,
        "resumen_id": nueva.resumen_id,
        "mes_periodo": nueva.mes_periodo,
        "anio_periodo": nueva.anio_periodo,
        "estado": nueva.estado,
        "nro_liquidacion": nueva.nro_liquidacion,
        "total_bruto": str(nueva.total_bruto or 0),
        "total_debitos": str(nueva.total_debitos or 0),
        "total_neto": str(nueva.total_neto or 0),
    })
