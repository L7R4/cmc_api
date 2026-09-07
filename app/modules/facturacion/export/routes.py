"""Endpoints de exportación (detalle + carátula) y de presets guardados.

Montado bajo el mismo prefix `/facturacion` que el resto del módulo (ver
`app/api/routes.py`) — no bajo `/exports`, porque depende de la lógica de
`facturacion.service` (`periodo_label`, constantes de tipo) y de su propio
scope; colgarlo de `exports` obligaría a un import cruzado entre módulos que
hoy no existe.
"""
import datetime
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.database import get_db
from app.db.models import ExportPreset
from app.modules.facturacion.export import armado as armado_mod
from app.modules.facturacion.export import caratula as caratula_mod
from app.modules.facturacion.export import datos as datos_mod
from app.modules.facturacion.export import encabezado as encabezado_mod
from app.modules.facturacion.export import excel as excel_mod
from app.modules.facturacion.export import pdf as pdf_mod
from app.modules.facturacion.export.schemas import (
    COLUMNAS_DEFAULT,
    AgrupacionExport,
    ColumnaExport,
    ExportOpciones,
    OrdenExport,
    PresetIn,
    PresetOut,
    TipoPrestacion,
)

router = APIRouter()
logger = logging.getLogger(__name__)


async def _resolver_opciones(
    preset_id: Optional[int] = Query(None, description="Ignora el resto de los parámetros si viene"),
    orden: OrdenExport = Query("nombre_socio"),
    agrupacion: AgrupacionExport = Query("todo_junto"),
    columnas: Optional[list[ColumnaExport]] = Query(None),
    fecha_desde: Optional[datetime.date] = Query(None),
    fecha_hasta: Optional[datetime.date] = Query(None),
    id_especialidad: Optional[int] = Query(None),
    cod_medicos: Optional[list[str]] = Query(None),
    revisado: Optional[bool] = Query(None),
    tipos: Optional[list[TipoPrestacion]] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
) -> ExportOpciones:
    if preset_id is not None:
        preset = await db.get(ExportPreset, preset_id)
        if preset is None or preset.usuario != str(user["nro_socio"]):
            raise HTTPException(404, "Preset no encontrado")
        return ExportOpciones(**preset.opciones)

    return ExportOpciones(
        orden=orden, agrupacion=agrupacion, columnas=columnas or list(COLUMNAS_DEFAULT),
        fecha_desde=fecha_desde, fecha_hasta=fecha_hasta, id_especialidad=id_especialidad,
        cod_medicos=cod_medicos, revisado=revisado, tipos=tipos,
    )


@router.get("/facturas/{id}/export/detalle.pdf")
async def export_detalle_pdf(
    id: int, opciones: ExportOpciones = Depends(_resolver_opciones), db: AsyncSession = Depends(get_db),
):
    t0 = time.perf_counter()
    factura = await datos_mod.obtener_factura(db, id)
    filas = await datos_mod.obtener_filas_export(db, factura, opciones)
    armado = armado_mod.armar(filas, opciones)
    encabezado = await encabezado_mod.construir_encabezado(db, factura)
    contenido = await run_in_threadpool(
        pdf_mod.build_pdf_detalle, armado, opciones, encabezado,
    )
    logger.info(
        "export detalle.pdf factura=%s filas=%s tiempo=%.2fs", id, len(filas), time.perf_counter() - t0,
    )
    return Response(
        content=contenido, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="detalle_factura_{id}.pdf"'},
    )


@router.get("/facturas/{id}/export/detalle.xlsx")
async def export_detalle_xlsx(
    id: int, opciones: ExportOpciones = Depends(_resolver_opciones), db: AsyncSession = Depends(get_db),
):
    t0 = time.perf_counter()
    factura = await datos_mod.obtener_factura(db, id)
    filas = await datos_mod.obtener_filas_export(db, factura, opciones)
    armado = armado_mod.armar(filas, opciones)
    encabezado = await encabezado_mod.construir_encabezado(db, factura)
    contenido = await run_in_threadpool(excel_mod.build_excel_detalle, armado, opciones, encabezado)
    logger.info(
        "export detalle.xlsx factura=%s filas=%s tiempo=%.2fs", id, len(filas), time.perf_counter() - t0,
    )
    return Response(
        content=contenido,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="detalle_factura_{id}.xlsx"'},
    )


@router.get("/facturas/{id}/export/caratula.pdf")
async def export_caratula_pdf(id: int, db: AsyncSession = Depends(get_db)):
    """Sin filtros: la carátula representa el total legal de la factura completa,
    no un subconjunto — a diferencia del detalle, no toma `ExportOpciones` de
    orden/agrupación/filtros."""
    t0 = time.perf_counter()
    factura = await datos_mod.obtener_factura(db, id)
    opciones = ExportOpciones()
    filas = await datos_mod.obtener_filas_export(db, factura, opciones)
    armado = armado_mod.armar(filas, opciones)
    ctx = await caratula_mod.construir_contexto(db, factura, armado)
    contenido = await run_in_threadpool(caratula_mod.build_pdf_caratula, ctx)
    logger.info("export caratula.pdf factura=%s filas=%s tiempo=%.2fs", id, len(filas), time.perf_counter() - t0)
    return Response(
        content=contenido, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="caratula_factura_{id}.pdf"'},
    )


@router.get("/facturas/{id}/export/caratula.xlsx")
async def export_caratula_xlsx(id: int, db: AsyncSession = Depends(get_db)):
    t0 = time.perf_counter()
    factura = await datos_mod.obtener_factura(db, id)
    opciones = ExportOpciones()
    filas = await datos_mod.obtener_filas_export(db, factura, opciones)
    armado = armado_mod.armar(filas, opciones)
    ctx = await caratula_mod.construir_contexto(db, factura, armado)
    contenido = await run_in_threadpool(caratula_mod.build_excel_caratula, ctx)
    logger.info("export caratula.xlsx factura=%s filas=%s tiempo=%.2fs", id, len(filas), time.perf_counter() - t0)
    return Response(
        content=contenido,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="caratula_factura_{id}.xlsx"'},
    )


# ── Presets ───────────────────────────────────────────────────────────────

@router.get("/export-presets", response_model=list[PresetOut])
async def listar_presets(
    tipo_documento: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db), user=Depends(get_current_user),
):
    stmt = select(ExportPreset).where(ExportPreset.usuario == str(user["nro_socio"]))
    if tipo_documento:
        stmt = stmt.where(ExportPreset.tipo_documento == tipo_documento)
    stmt = stmt.order_by(ExportPreset.created_at.desc())
    return (await db.execute(stmt)).scalars().all()


@router.post("/export-presets", response_model=PresetOut, status_code=201)
async def crear_preset(
    payload: PresetIn, db: AsyncSession = Depends(get_db), user=Depends(get_current_user),
):
    preset = ExportPreset(
        usuario=str(user["nro_socio"]), nombre=payload.nombre,
        tipo_documento=payload.tipo_documento,
        opciones=payload.opciones.model_dump(mode="json"),
    )
    db.add(preset)
    await db.commit()
    await db.refresh(preset)
    return preset


@router.delete("/export-presets/{preset_id}", status_code=204)
async def eliminar_preset(
    preset_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user),
):
    preset = await db.get(ExportPreset, preset_id)
    if preset is None or preset.usuario != str(user["nro_socio"]):
        raise HTTPException(404, "Preset no encontrado")
    await db.delete(preset)
    await db.commit()
