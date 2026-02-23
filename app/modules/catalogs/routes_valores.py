from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import ObrasSociales, ValorPrestacion
from app.modules.catalogs.schemas import ValorPrestacionOut

router = APIRouter()


@router.get(
    "/boletin",
    response_model=List[ValorPrestacionOut],
    summary="Listar valores de prestación (primero por código y obra social)",
)
async def listar_valores_boletin(
    db: AsyncSession = Depends(get_db),
    nro_obra_social: Optional[int] = Query(None, description="Nro de obra social"),
    codigo: Optional[str] = Query(None, description="Código de prestación"),
    page: int = Query(1, ge=1, description="Número de página"),
    size: int = Query(50, ge=1, le=500, description="Registros por página"),
):
    # Por cada par (NRO_OBRASOCIAL, CODIGOS) puede haber duplicados; tomamos el de menor ID.
    subq = (
        select(func.min(ValorPrestacion.ID).label("min_id"))
        .group_by(ValorPrestacion.NRO_OBRASOCIAL, ValorPrestacion.CODIGOS)
        .subquery()
    )

    stmt = (
        select(
            ValorPrestacion.ID.label("id"),
            ValorPrestacion.CODIGOS.label("codigos"),
            ValorPrestacion.NRO_OBRASOCIAL.label("nro_obrasocial"),
            ObrasSociales.OBRA_SOCIAL.label("obra_social"),
            ValorPrestacion.HONORARIOS_A.label("honorarios_a"),
            ValorPrestacion.HONORARIOS_B.label("honorarios_b"),
            ValorPrestacion.HONORARIOS_C.label("honorarios_c"),
            ValorPrestacion.GASTOS.label("gastos"),
            ValorPrestacion.AYUDANTE_A.label("ayudante_a"),
            ValorPrestacion.AYUDANTE_B.label("ayudante_b"),
            ValorPrestacion.AYUDANTE_C.label("ayudante_c"),
            ValorPrestacion.C_P_H_S.label("c_p_h_s"),
            ValorPrestacion.FECHA_CAMBIO.label("fecha_cambio"),
        )
        .select_from(ValorPrestacion)
        .join(subq, ValorPrestacion.ID == subq.c.min_id)
        .join(
            ObrasSociales,
            ObrasSociales.NRO_OBRASOCIAL == ValorPrestacion.NRO_OBRASOCIAL,
            isouter=True,
        )
    )

    if nro_obra_social is not None:
        stmt = stmt.where(ValorPrestacion.NRO_OBRASOCIAL == nro_obra_social)
    if codigo is not None:
        stmt = stmt.where(ValorPrestacion.CODIGOS == codigo)

    stmt = (
        stmt
        .order_by(ValorPrestacion.NRO_OBRASOCIAL.asc(), ValorPrestacion.CODIGOS.asc())
        .offset((page - 1) * size)
        .limit(size)
    )

    rows = (await db.execute(stmt)).mappings().all()
    return [ValorPrestacionOut(**row) for row in rows]
