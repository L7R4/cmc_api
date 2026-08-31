"""Panel de Cobranzas — deuda por concepto. Solo lectura.

Montado en `/api/cobranzas` (ver `app/api/routes.py`). Autorización declarada
en `app/auth/authz.py` (scope `cobranza:leer`), no acá — este router no lleva
`require_scope` porque el proyecto usa una matriz central (`SCOPES_POR_RUTA`).

Orden de declaración: `/export` y `/por_concepto` van antes de cualquier ruta
con parámetro dinámico, para no repetir el bug que hoy deja
`GET /api/deducciones/top-deudores` inalcanzable (`routes.py:466` vs `:541`).
"""
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.modules.deducciones.schemas import (
    CobranzaExportRow,
    CobranzaMedicosPage,
    CobranzaMedicoDetalle,
    CobranzaPorConceptoItem,
    CobranzasResumen,
    CobranzaSociosPage,
)
from app.modules.deducciones.service_cobranzas import (
    CobranzasFiltros,
    get_detalle_medico,
    get_export_rows,
    get_medicos_por_concepto,
    get_por_concepto,
    get_resumen,
    get_socios_deudores,
)

router = APIRouter()


def _filtros(
    q: Optional[str] = Query(None, description="Nombre/nro de concepto o de médico"),
    mes_desde: Optional[int] = Query(None, ge=1, le=12),
    anio_desde: Optional[int] = Query(None, ge=2000),
    mes_hasta: Optional[int] = Query(None, ge=1, le=12),
    anio_hasta: Optional[int] = Query(None, ge=2000),
    paga_por_caja: Optional[bool] = Query(None, description="true=solo caja | false=solo liquidación"),
    incluir_futuros: bool = Query(False, description="Incluir cuotas de períodos futuros a hoy"),
    saldo_min: Optional[Decimal] = Query(None, ge=0),
    # Se llama `concepto_id` y no `descuento_id` porque `_filtros` se inyecta
    # también en /por_concepto/{descuento_id}/medicos, donde ese nombre ya es
    # path param: FastAPI no admite un Query y un Path homónimos.
    concepto_id: Optional[int] = Query(None, description="Acota la deuda a un solo concepto"),
) -> CobranzasFiltros:
    return CobranzasFiltros(
        q=q,
        mes_desde=mes_desde,
        anio_desde=anio_desde,
        mes_hasta=mes_hasta,
        anio_hasta=anio_hasta,
        paga_por_caja=paga_por_caja,
        incluir_futuros=incluir_futuros,
        saldo_min=saldo_min,
        descuento_id=concepto_id,
    )


@router.get("/resumen", response_model=CobranzasResumen)
async def resumen(
    filtros: CobranzasFiltros = Depends(_filtros),
    db: AsyncSession = Depends(get_db),
):
    return await get_resumen(db, filtros)


@router.get("/por_concepto", response_model=List[CobranzaPorConceptoItem])
async def por_concepto(
    filtros: CobranzasFiltros = Depends(_filtros),
    db: AsyncSession = Depends(get_db),
):
    return await get_por_concepto(db, filtros)


@router.get("/por_socio", response_model=CobranzaSociosPage)
async def por_socio(
    page: int = Query(1, ge=1),
    size: int = Query(25, ge=1, le=200),
    filtros: CobranzasFiltros = Depends(_filtros),
    db: AsyncSession = Depends(get_db),
):
    """Deuda consolidada por médico, cruzando conceptos. Paginado."""
    total, items = await get_socios_deudores(db, filtros, page, size)
    return CobranzaSociosPage(total=total, page=page, size=size, items=items)


@router.get("/export", response_model=List[CobranzaExportRow])
async def export(
    filtros: CobranzasFiltros = Depends(_filtros),
    db: AsyncSession = Depends(get_db),
):
    """Sin paginación — para exportar a Excel/PDF."""
    return await get_export_rows(db, filtros)


@router.get("/por_concepto/{descuento_id}/medicos", response_model=CobranzaMedicosPage)
async def medicos_por_concepto(
    descuento_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    filtros: CobranzasFiltros = Depends(_filtros),
    db: AsyncSession = Depends(get_db),
):
    nombre, total, items = await get_medicos_por_concepto(db, descuento_id, filtros, page, size)
    if nombre is None:
        raise HTTPException(404, "Concepto no encontrado")
    return CobranzaMedicosPage(
        descuento_id=descuento_id,
        descuento_nombre=nombre,
        total=total,
        page=page,
        size=size,
        items=items,
    )


@router.get("/medicos/{medico_id}", response_model=CobranzaMedicoDetalle)
async def detalle_medico(
    medico_id: int,
    filtros: CobranzasFiltros = Depends(_filtros),
    db: AsyncSession = Depends(get_db),
):
    detalle = await get_detalle_medico(db, medico_id, filtros)
    if detalle is None:
        raise HTTPException(404, "Médico no encontrado")
    return detalle
