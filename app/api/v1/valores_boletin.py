from datetime import date
from typing import List, Optional, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db
from app.db.models import ValoresBoletin, ObrasSociales
from app.schemas.valores_boletin_schema import ValoresBoletinOut

router = APIRouter()

@router.get(
    "/boletin",
    response_model=List[ValoresBoletinOut],
    summary="Listar valores de boletín con filtros"
)
async def listar_valores_boletin(
    db: AsyncSession = Depends(get_async_db),
    nro_obrasocial: Optional[int] = Query(None, description="Nro de obra social"),
    nivel: Optional[int] = Query(None, ge=0, description="Nivel"),
    fecha_cambio: Optional[date] = Query(None, description="Fecha de cambio exacta (YYYY-MM-DD)"),
    categoria: Optional[Literal["A","B","C","a","b","c"]] = Query(None, description="Filtrar por categoría declarada (A/B/C)"),
):
    os_num = getattr(ObrasSociales, "NRO_OBRASOCIAL", None)
    os_name = getattr(ObrasSociales, "OBRA_SOCIAL", None)

    stmt = (
        select(
            ValoresBoletin.ID.label("id"),
            ValoresBoletin.NRO_OBRASOCIAL.label("nro_obrasocial"),
            os_name.label("obra_social"),
            ValoresBoletin.NIVEL.label("nivel"),
            ValoresBoletin.FECHA_CAMBIO.label("fecha_cambio"),
            ValoresBoletin.CONSULTA.label("consulta"),
            ValoresBoletin.GALENO_QUIRURGICO.label("galeno_quirurgico"),
            ValoresBoletin.GASTOS_QUIRURGICOS.label("gastos_quirurgicos"),
            ValoresBoletin.GALENO_PRACTICA.label("galeno_practica"),
            ValoresBoletin.GALENO_RADIOLOGICO.label("galeno_radiologico"),
            ValoresBoletin.GASTOS_RADIOLOGICO.label("gastos_radiologico"),
            ValoresBoletin.GASTOS_BIOQUIMICOS.label("gastos_bioquimicos"),
            ValoresBoletin.OTROS_GASTOS.label("otros_gastos"),
            ValoresBoletin.GALENO_CIRUGIA_ADULTOS.label("galeno_cirugia_adultos"),
            ValoresBoletin.GALENO_CIRUGIA_INFANTIL.label("galeno_cirugia_infantil"),
            ValoresBoletin.CONSULTA_ESPECIAL.label("consulta_especial"),
            ValoresBoletin.CATEGORIA_A.label("categoria_a"),
            ValoresBoletin.CATEGORIA_B.label("categoria_b"),
            ValoresBoletin.CATEGORIA_C.label("categoria_c"),
        )
        .select_from(ValoresBoletin)
        .join(ObrasSociales, os_num == ValoresBoletin.NRO_OBRASOCIAL, isouter=True)
    )

    #region Filtros opcionales
    if nro_obrasocial is not None:
        stmt = stmt.where(ValoresBoletin.NRO_OBRASOCIAL == nro_obrasocial)

    if nivel is not None:
        stmt = stmt.where(ValoresBoletin.NIVEL == nivel)

    if fecha_cambio is not None:
        stmt = stmt.where(ValoresBoletin.FECHA_CAMBIO == fecha_cambio)

    if categoria is not None:
        cat = categoria.upper()
        stmt = stmt.where(
            or_(
                func.upper(ValoresBoletin.CATEGORIA_A) == cat,
                func.upper(ValoresBoletin.CATEGORIA_B) == cat,
                func.upper(ValoresBoletin.CATEGORIA_C) == cat,
            )
        )

    #endregion
    
    
    stmt = stmt.order_by(ValoresBoletin.NRO_OBRASOCIAL.asc(),
                         ValoresBoletin.NIVEL.asc(),
                         ValoresBoletin.FECHA_CAMBIO.asc())

    rows = (await db.execute(stmt)).mappings().all()
    return [ValoresBoletinOut(**row) for row in rows]
