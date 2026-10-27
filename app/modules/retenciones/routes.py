"""
Retenciones que el Colegio practica como agente. Hoy, Ganancias (concepto 300):

  GET /ganancias?anio=&mes=   listado mensual por socio (pagos cerrados del mes): de acá salen el
                              PDF y el Excel para el contador y los comprobantes de cada socio.
  GET /ganancias/escalas      escalas cargadas (mínimo no imponible y tramos, por vigencia).

El cálculo lo hace el pago (app/modules/retenciones/service.py), no estas rutas.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.database import get_db
from app.db.models import RetencionGananciasEscala
from app.modules.retenciones.schemas import EscalaRead, RetencionGananciasListado, TramoRead
from app.modules.retenciones.service import listado_mensual

router = APIRouter()


@router.get("/ganancias", response_model=RetencionGananciasListado)
async def listado_ganancias(
    anio: int = Query(..., ge=1900, le=3000),
    mes: int = Query(..., ge=1, le=12),
    db: AsyncSession = Depends(get_db),
):
    return await listado_mensual(db, anio, mes)


@router.get("/ganancias/escalas", response_model=list[EscalaRead])
async def escalas_ganancias(db: AsyncSession = Depends(get_db)):
    filas = (await db.execute(
        select(RetencionGananciasEscala)
        .options(selectinload(RetencionGananciasEscala.tramos))
        .order_by(RetencionGananciasEscala.tipo_sujeto, RetencionGananciasEscala.vigente_desde.desc())
    )).scalars().all()
    return [
        EscalaRead(
            id=e.id,
            tipo_sujeto=e.tipo_sujeto,
            vigente_desde=e.vigente_desde.isoformat(),
            minimo_no_imponible=e.minimo_no_imponible,
            retencion_minima=e.retencion_minima,
            observacion=e.observacion,
            tramos=[TramoRead(desde=t.desde, hasta=t.hasta, importe_fijo=t.importe_fijo, porcentaje=t.porcentaje)
                    for t in e.tramos],
        )
        for e in filas
    ]
