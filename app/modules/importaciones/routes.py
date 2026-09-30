"""Endpoints de importación masiva. Ver el docstring del paquete."""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.database import get_db
from app.modules.importaciones.prevencion import servicio as prevencion
from app.modules.importaciones.swiss import servicio as swiss
from app.modules.importaciones.schemas import (
    ImportacionIn,
    ImportacionOut,
    PeriodosOut,
)

log = logging.getLogger(__name__)

router = APIRouter()


def _usuario(user) -> str:
    """Quién está importando, para la columna `usuario` del detalle."""
    return str(getattr(user, "NRO_SOCIO", None) or getattr(user, "id", "") or "")


@router.get("/prevencion/periodos", response_model=PeriodosOut)
async def periodos_prevencion(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Períodos entre los que elegir, con el sugerido marcado.

    El administrativo decide en cuál entra el reporte: el rango que declara el
    archivo ("21/07 al 20/08") cruza dos meses, así que derivarlo solo sería
    adivinar.
    """
    return await prevencion.periodos_disponibles(db)


@router.post("/prevencion/previsualizar", response_model=ImportacionOut)
async def previsualizar_prevencion(
    body: ImportacionIn,
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
    )


@router.post("/prevencion/confirmar", response_model=ImportacionOut)
async def confirmar_prevencion(
    body: ImportacionIn,
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
    )
    log.info(
        "Import Prevención: %s de %s filas grabadas en el período %s por el usuario %s "
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
