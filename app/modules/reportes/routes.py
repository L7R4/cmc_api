"""Reportes y Estadísticas.

Dos routers, y la diferencia entre ellos es de seguridad, no de comodidad:

* `router` — para el Colegio. Exige el scope **`facturas:ver`** (hoy sólo lo
  tiene el rol `admin`), porque cruza la facturación de TODOS los médicos: quién
  facturó más, qué códigos, contra qué obra social. Es información sensible
  entre colegas.
* `router_socio` — para el médico. Sólo autenticación, y **el `nro_socio` sale
  del token**: no hay ningún parámetro para pedir los datos de otro. Un médico
  ve exactamente lo suyo y nada más.

Ese es el motivo de tener dos routers en vez de uno con un `if`: si el filtro
por médico fuera un parámetro opcional, un olvido en un endpoint expondría la
facturación de todo el Colegio. Acá el filtro no es opcional, es estructural.

El módulo es de SOLO LECTURA: no escribe en ninguna tabla.
"""
import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_scope
from app.db.database import get_db
from app.modules.reportes import service
from app.modules.reportes.schemas import (
    CodigoStatOut,
    MedicoStatOut,
    ObraSocialStatOut,
    PaginaPrestaciones,
    PuntoSerieOut,
    ResumenOut,
)

SCOPE_REPORTES = "facturas:ver"

router = APIRouter(dependencies=[Depends(require_scope(SCOPE_REPORTES))])
router_socio = APIRouter()

# YYYYMM. Se valida en el Query para que una cadena rara no llegue al WHERE.
PERIODO = Query(..., min_length=6, max_length=6, pattern=r"^\d{6}$", description="YYYYMM")
PERIODO_OPC = Query(None, min_length=6, max_length=6, pattern=r"^\d{6}$")


def _socio_del_token(user: dict) -> str:
    """El médico logueado. Nunca sale de un parámetro del request."""
    return str(user["nro_socio"])


# ═══════════════════════ Colegio (scope facturas:ver) ═══════════════════════

@router.get("/resumen", response_model=ResumenOut)
async def resumen(
    periodo: str = PERIODO,
    db: AsyncSession = Depends(get_db),
):
    """KPIs del período: prestaciones, importes, y cuántos médicos, obras
    sociales y códigos tuvieron movimiento."""
    return await service.resumen(db, periodo=periodo)


@router.get("/codigos", response_model=List[CodigoStatOut])
async def codigos(
    periodo: str = PERIODO,
    especialidad_id: Optional[int] = Query(None, description="ID_COLEGIO_ESPE de la prestación"),
    obra_social: Optional[str] = Query(None),
    q: Optional[str] = Query(None, max_length=80, description="Busca en código y descripción"),
    orden: str = Query("importe", pattern="^(importe|cantidad|prestaciones|codigo)$"),
    limit: int = Query(50, ge=1, le=service.MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Tabla de códigos del período, con lo facturado de cada uno.

    Filtrable por especialidad y por obra social, y ordenable por importe,
    cantidad o código.
    """
    return await service.por_codigo(
        db,
        periodo=periodo,
        especialidad_id=especialidad_id,
        obra_social=obra_social,
        q=q,
        orden=orden,
        limit=limit,
        offset=offset,
    )


@router.get("/codigos/{codigo}/medicos", response_model=List[MedicoStatOut])
async def medicos_de_codigo(
    codigo: str,
    periodo: str = PERIODO,
    obra_social: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=service.MAX_LIMIT),
    db: AsyncSession = Depends(get_db),
):
    """Todos los que facturaron un código en el período, de mayor a menor."""
    return await service.por_medico(
        db, periodo=periodo, codigo=codigo, obra_social=obra_social, limit=limit
    )


@router.get("/medicos", response_model=List[MedicoStatOut])
async def medicos(
    periodo: str = PERIODO,
    obra_social: Optional[str] = Query(None, description="Ranking dentro de una O.S."),
    q: Optional[str] = Query(None, max_length=80, description="Busca por nombre o nro de socio"),
    limit: int = Query(50, ge=1, le=service.MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Médicos que más facturaron en el período. Con `obra_social`, quiénes más
    facturaron a esa obra social."""
    return await service.por_medico(
        db, periodo=periodo, obra_social=obra_social, q=q, limit=limit, offset=offset
    )


@router.get("/medicos/{nro_socio}/codigos", response_model=List[CodigoStatOut])
async def codigos_de_medico(
    nro_socio: str,
    periodo: str = PERIODO,
    obra_social: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=service.MAX_LIMIT),
    db: AsyncSession = Depends(get_db),
):
    """Qué facturó un médico, abierto por código."""
    return await service.por_codigo(
        db, periodo=periodo, nro_socio=nro_socio, obra_social=obra_social, limit=limit
    )


@router.get("/obras-sociales", response_model=List[ObraSocialStatOut])
async def obras_sociales(
    periodo: str = PERIODO,
    limit: int = Query(50, ge=1, le=service.MAX_LIMIT),
    db: AsyncSession = Depends(get_db),
):
    """Ranking de obras sociales por lo facturado en el período."""
    return await service.por_obra_social(db, periodo=periodo, limit=limit)


@router.get("/prestaciones", response_model=PaginaPrestaciones)
async def prestaciones(
    obra_social: Optional[str] = Query(None),
    periodo: Optional[str] = PERIODO_OPC,
    nro_socio: Optional[str] = Query(None),
    codigo: Optional[str] = Query(None),
    validacion_estado: Optional[str] = Query(
        None,
        pattern="^(autorizada|rechazada|pendiente|cargada)$",
        description="Estado que devolvió la O.S. Vacío = todas.",
    ),
    desde: Optional[datetime.date] = Query(None),
    hasta: Optional[datetime.date] = Query(None),
    limit: int = Query(50, ge=1, le=service.MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Listado prestación por prestación: por obra social, fecha, código y
    estado de validación (autorizada / no autorizada).

    Es el único endpoint que devuelve filas y no agregados, por eso va paginado
    y con tope duro de `limit`.
    """
    return await service.prestaciones(
        db,
        obra_social=obra_social,
        periodo=periodo,
        nro_socio=nro_socio,
        codigo=codigo,
        validacion_estado=validacion_estado,
        desde=desde,
        hasta=hasta,
        limit=limit,
        offset=offset,
    )


@router.get("/evolucion", response_model=List[PuntoSerieOut])
async def evolucion(
    obra_social: Optional[str] = Query(None),
    meses: int = Query(12, ge=1, le=36),
    db: AsyncSession = Depends(get_db),
):
    """Serie por período, para el gráfico de evolución."""
    return await service.evolucion(db, obra_social=obra_social, meses=meses)


# ═══════════════ Médico (sólo autenticación · siempre lo propio) ═════════════

@router_socio.get("/mios/resumen", response_model=ResumenOut)
async def mi_resumen(
    periodo: str = PERIODO,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Mis números del período."""
    return await service.resumen(db, periodo=periodo, nro_socio=_socio_del_token(user))


@router_socio.get("/mios/codigos", response_model=List[CodigoStatOut])
async def mis_codigos(
    periodo: str = PERIODO,
    obra_social: Optional[str] = Query(None),
    orden: str = Query("importe", pattern="^(importe|cantidad|prestaciones|codigo)$"),
    limit: int = Query(50, ge=1, le=service.MAX_LIMIT),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Qué facturé, abierto por código."""
    return await service.por_codigo(
        db,
        periodo=periodo,
        nro_socio=_socio_del_token(user),
        obra_social=obra_social,
        orden=orden,
        limit=limit,
    )


@router_socio.get("/mios/obras-sociales", response_model=List[ObraSocialStatOut])
async def mis_obras_sociales(
    periodo: str = PERIODO,
    limit: int = Query(50, ge=1, le=service.MAX_LIMIT),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """A qué obras sociales le facturé, de mayor a menor."""
    return await service.por_obra_social(
        db, periodo=periodo, nro_socio=_socio_del_token(user), limit=limit
    )


@router_socio.get("/mios/prestaciones", response_model=PaginaPrestaciones)
async def mis_prestaciones(
    periodo: Optional[str] = PERIODO_OPC,
    obra_social: Optional[str] = Query(None),
    codigo: Optional[str] = Query(None),
    desde: Optional[datetime.date] = Query(None),
    hasta: Optional[datetime.date] = Query(None),
    limit: int = Query(50, ge=1, le=service.MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Mis prestaciones, una por una."""
    return await service.prestaciones(
        db,
        nro_socio=_socio_del_token(user),
        periodo=periodo,
        obra_social=obra_social,
        codigo=codigo,
        desde=desde,
        hasta=hasta,
        limit=limit,
        offset=offset,
    )


@router_socio.get("/mios/evolucion", response_model=List[PuntoSerieOut])
async def mi_evolucion(
    meses: int = Query(12, ge=1, le=36),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Cómo vengo mes a mes."""
    return await service.evolucion(
        db, nro_socio=_socio_del_token(user), meses=meses
    )
