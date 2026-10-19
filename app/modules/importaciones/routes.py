"""Endpoints de importación masiva. Ver el docstring del paquete."""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.database import get_db
from app.modules.importaciones.planilla import estado, servicio as planilla
from app.modules.importaciones.planilla.plantilla import generar_plantilla
from app.modules.importaciones.planilla.schemas import LoteOut, PeriodosPlanillaOut, PlanillaIn, PlanillaOut
from app.modules.importaciones.prevencion import servicio as prevencion
from app.modules.importaciones.swiss import servicio as swiss
from app.modules.importaciones.unne import servicio as unne
from app.modules.importaciones.schemas import (
    ImportacionIn,
    ImportacionOut,
    ImportacionPrevencionIn,
    ImportacionUnneIn,
    PeriodosOut,
)

log = logging.getLogger(__name__)

router = APIRouter()


def _usuario(user) -> str:
    """Quién está importando, para la columna `usuario` del detalle."""
    return str(getattr(user, "NRO_SOCIO", None) or getattr(user, "id", "") or "")


@router.get("/prevencion/periodos", response_model=PeriodosOut)
async def periodos_prevencion(
    obra_social: Optional[int] = Query(None, description="103 (default) u 888, la de prueba"),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Períodos entre los que elegir, con el sugerido marcado.

    El administrativo decide en cuál entra el reporte: el rango que declara el
    archivo ("21/07 al 20/08") cruza dos meses, así que derivarlo solo sería
    adivinar.
    """
    return await prevencion.periodos_disponibles(db, obra_social)


@router.post("/prevencion/previsualizar", response_model=ImportacionOut)
async def previsualizar_prevencion(
    body: ImportacionPrevencionIn,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Qué se grabaría del reporte. **No escribe nada.**

    Devuelve una fila por práctica con su médico resuelto, su precio y, si no
    entra, el motivo. Es el paso donde se ve una matrícula que no cayó en
    ningún socio o un código que la obra social no tiene cotizado, antes de
    que exista una sola fila.
    """
    return await prevencion.procesar(
        db,
        filas=body.filas,
        periodo=body.periodo,
        usuario_carga=_usuario(user),
        grabar=False,
        obra_social=body.obra_social,
    )


@router.post("/prevencion/confirmar", response_model=ImportacionOut)
async def confirmar_prevencion(
    body: ImportacionPrevencionIn,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Graba en `detalle_facturacion` lo que la previsualización marcó grabable.

    Todo en un commit: o entra el reporte entero o no entra nada. Reenviar el
    mismo archivo no duplica — las filas ya cargadas en el período vuelven
    marcadas como `duplicada` y se saltean.
    """
    usuario = _usuario(user)
    salida = await prevencion.procesar(
        db,
        filas=body.filas,
        periodo=body.periodo,
        usuario_carga=usuario,
        grabar=True,
        obra_social=body.obra_social,
    )
    log.info(
        "Import Prevención (O.S. %s): %s de %s filas grabadas en el período %s por el usuario %s "
        "(archivo %r, %s duplicadas, %s omitidas).",
        body.obra_social,
        salida.resumen.grabables,
        salida.resumen.total,
        salida.resumen.periodo,
        usuario,
        body.archivo,
        salida.resumen.duplicadas,
        salida.resumen.omitidas,
    )
    return salida


# ── Swiss Medical ─────────────────────────────────────────────────────────────


@router.get("/swiss/periodos", response_model=PeriodosOut)
async def periodos_swiss(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Períodos entre los que elegir, con el sugerido marcado."""
    return await swiss.periodos_disponibles(db)


@router.post("/swiss/previsualizar", response_model=ImportacionOut)
async def previsualizar_swiss(
    body: ImportacionIn,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Qué se grabaría del reporte de liquidación. **No escribe nada.**

    Acá se ven los dos problemas propios de Swiss: los códigos de ocho dígitos
    que hubo que traducir y las matrículas de otra provincia, que no se pueden
    resolver contra el padrón del Colegio.
    """
    return await swiss.procesar(
        db,
        filas=body.filas,
        periodo=body.periodo,
        usuario_carga=_usuario(user),
        grabar=False,
    )


@router.post("/swiss/confirmar", response_model=ImportacionOut)
async def confirmar_swiss(
    body: ImportacionIn,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Graba en `detalle_facturacion` lo que la previsualización marcó grabable."""
    usuario = _usuario(user)
    salida = await swiss.procesar(
        db,
        filas=body.filas,
        periodo=body.periodo,
        usuario_carga=usuario,
        grabar=True,
    )
    log.info(
        "Import Swiss: %s de %s filas grabadas en el período %s por el usuario %s "
        "(archivo %r, %s duplicadas, %s omitidas).",
        salida.resumen.grabables,
        salida.resumen.total,
        salida.resumen.periodo,
        usuario,
        body.archivo,
        salida.resumen.duplicadas,
        salida.resumen.omitidas,
    )
    return salida


# ── UNNE ──────────────────────────────────────────────────────────────────────


@router.get("/unne/periodos", response_model=PeriodosOut)
async def periodos_unne(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Períodos entre los que elegir, con el sugerido marcado."""
    return await unne.periodos_disponibles(db)


@router.post("/unne/previsualizar", response_model=ImportacionOut)
async def previsualizar_unne(
    body: ImportacionUnneIn,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Qué se grabaría del Excel de UNNE. **No escribe nada.**

    Las filas cuya matrícula no cae en un único socio vuelven en `elegir_socio`
    con los candidatos, para elegir antes de confirmar.
    """
    return await unne.procesar(
        db, filas=body.filas, periodo=body.periodo, usuario_carga=_usuario(user), grabar=False,
    )


@router.post("/unne/confirmar", response_model=ImportacionOut)
async def confirmar_unne(
    body: ImportacionUnneIn,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Graba lo grabable en un solo commit. 422 si queda alguna fila sin socio."""
    usuario = _usuario(user)
    salida = await unne.procesar(
        db, filas=body.filas, periodo=body.periodo, usuario_carga=usuario, grabar=True,
    )
    log.info(
        "Import UNNE: %s de %s filas grabadas en el período %s por el usuario %s "
        "(archivo %r, %s duplicadas, %s omitidas).",
        salida.resumen.grabables, salida.resumen.total, salida.resumen.periodo, usuario,
        body.archivo, salida.resumen.duplicadas, salida.resumen.omitidas,
    )
    return salida


# ── Planilla del Colegio ──────────────────────────────────────────────────────


def _nro_socio(user: dict) -> str:
    """Quién importa, para `usuario` del detalle: igual que la carga manual de facturación."""
    return str(user["nro_socio"])


@router.get("/planilla/plantilla")
async def plantilla_planilla(user=Depends(get_current_user)):
    """La plantilla .xlsx vacía, generada con las columnas vigentes."""
    return Response(
        content=generar_plantilla(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="plantilla_importacion_prestaciones.xlsx"'},
    )


@router.get("/planilla/periodos", response_model=PeriodosPlanillaOut)
async def periodos_planilla(
    obra_social: int = Query(..., description="NRO_OBRASOCIAL destino de la carga"),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Períodos conocidos de esa obra social, el sugerido y el primero en que se puede cargar."""
    return await planilla.periodos(db, obra_social)


@router.post("/planilla/previsualizar", response_model=PlanillaOut)
async def previsualizar_planilla(
    body: PlanillaIn,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Qué se grabaría de la planilla. **No escribe nada.**

    Cada fila se valida con las mismas reglas que la carga manual; lo que no entra
    vuelve con su motivo.
    """
    return await planilla.procesar(db, datos=body, usuario=_nro_socio(user), grabar=False)


@router.post("/planilla/confirmar", response_model=PlanillaOut)
async def confirmar_planilla(
    body: PlanillaIn,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Graba lo que la previsualización marcó grabable, **todo o nada**: un único commit.

    Exige `lote`. Se puede seguir (`GET /planilla/lotes/{lote}`) y cancelar
    (`POST /planilla/lotes/{lote}/cancelar`) mientras corre; cancelada responde 409 y no
    graba nada. Ver `planilla/estado.py`.
    """
    usuario = _nro_socio(user)
    lote = body.lote
    if not lote:
        raise HTTPException(422, "Falta el lote de la importación.")
    estado.limpiar_viejos()

    async def cancelado() -> bool:
        return estado.cancelacion_pedida(lote)

    def progreso(fase: str, hecho: int, total: int) -> None:
        estado.escribir(lote, fase, hecho, total, forzar=hecho in (0, total))

    try:
        await _revisar_antes(cancelado)
        estado.escribir(lote, "validando", 0, len(body.filas), forzar=True)
        salida = await planilla.procesar(
            db, datos=body, usuario=usuario, grabar=True, cancelado=cancelado, progreso=progreso,
        )
    except planilla.ImportacionCancelada:
        await db.rollback()
        estado.escribir(lote, "cancelada", forzar=True)
        log.info("Import planilla cancelada por el usuario %s (O.S. %s, lote %s).", usuario, body.obra_social, lote)
        raise HTTPException(409, "Importación cancelada: no se grabó nada.")
    except BaseException as e:
        detalle = e.detail if isinstance(e, HTTPException) and isinstance(e.detail, str) else type(e).__name__
        estado.escribir(lote, "error", forzar=True, detalle=detalle)
        raise
    estado.escribir(lote, "grabada", salida.resumen.grabables, salida.resumen.total, forzar=True)
    log.info(
        "Import planilla (O.S. %s): %s de %s filas grabadas en el período %s por el usuario %s "
        "(archivo %r, lote %s, %s repetidas, %s omitidas).",
        body.obra_social, salida.resumen.grabables, salida.resumen.total, salida.resumen.periodo,
        usuario, body.archivo, lote, salida.resumen.duplicadas, salida.resumen.omitidas,
    )
    return salida


async def _revisar_antes(cancelado) -> None:
    """Cancelar antes de que la importación empiece también vale."""
    if await cancelado():
        raise planilla.ImportacionCancelada()


def _lote_o_422(lote: str) -> str:
    if not estado.lote_valido(lote):
        raise HTTPException(422, "Lote inválido.")
    return lote


@router.post("/planilla/lotes/{lote}/cancelar", status_code=202)
async def cancelar_planilla(lote: str, user=Depends(get_current_user)):
    """Pide cancelar la importación de ese lote. Responde enseguida: el resultado lo
    informa la propia importación (409 si frenó a tiempo; 200 si el commit ya había
    empezado), y se puede consultar en `GET /planilla/lotes/{lote}`."""
    estado.pedir_cancelacion(_lote_o_422(lote))
    return {"lote": lote, "pedida": True}


@router.get("/planilla/lotes/{lote}", response_model=LoteOut)
async def lote_planilla(
    lote: str,
    obra_social: int = Query(...),
    periodo: str = Query(..., pattern=r"^\d{6}$"),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Avance de la importación y cuántas prestaciones quedaron grabadas con ese lote.

    Sirve para dibujar el progreso y para saber qué pasó si la respuesta de «confirmar»
    no llegó (por ejemplo, un corte de red).
    """
    _lote_o_422(lote)
    e = estado.leer(lote) or {}
    contar = e.get("estado") in (None, "grabada", "guardando")
    base = (
        await planilla.estado_lote(db, obra_social=obra_social, periodo=periodo, lote=lote)
        if contar else LoteOut(lote=lote)
    )
    return LoteOut(
        lote=lote, estado=e.get("estado", "desconocido"), hecho=e.get("hecho", 0), total=e.get("total", 0),
        grabadas=base.grabadas, importe_total=base.importe_total,
    )
