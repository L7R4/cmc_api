from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import Liquidacion, Periodos

router = APIRouter()


@router.get("/disponibles")
async def periodos_disponibles(
    obra_social_id: int = Query(..., alias="obra_social_id"),
    anio: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    subq = (
        select(Liquidacion.id)
        .where(
            Liquidacion.obra_social_id == obra_social_id,
            Liquidacion.anio_periodo == Periodos.ANIO,
            Liquidacion.mes_periodo == Periodos.MES,
        )
        .limit(1)
    )
    stmt = (
        select(
            Periodos.ANIO.label("ANIO"),
            Periodos.MES.label("MES"),
            Periodos.NRO_FACT_1.label("NRO_FACT_1"),
            Periodos.NRO_FACT_2.label("NRO_FACT_2"),
            Periodos.CERRADO.label("CERRADO"),
        )
        .where(
            Periodos.NRO_OBRA_SOCIAL == obra_social_id,
            Periodos.CERRADO == "C",
            not_(subq.exists()),
        )
        .order_by(Periodos.ANIO.desc(), Periodos.MES.asc())
    )
    if anio is not None:
        stmt = stmt.where(Periodos.ANIO == anio)
    rows = (await db.execute(stmt)).mappings().all()
    return [dict(r) for r in rows]
