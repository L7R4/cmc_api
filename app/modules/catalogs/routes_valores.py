from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import ObrasSociales, ValorPrestacion, ValoresBoletin
from app.modules.catalogs.schemas import ValorPrestacionOut, ValoresBoletinOut

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
    # Por cada par (NRO_OBRASOCIAL, CODIGOS) puede haber duplicados;
    # tomamos el de FECHA_CAMBIO más reciente; en caso de empate, el de menor ID.
    fecha_subq = (
        select(
            ValorPrestacion.NRO_OBRASOCIAL.label("nos"),
            ValorPrestacion.CODIGOS.label("cod"),
            func.max(ValorPrestacion.FECHA_CAMBIO).label("max_fecha"),
        )
        .group_by(ValorPrestacion.NRO_OBRASOCIAL, ValorPrestacion.CODIGOS)
        .subquery()
    )
    subq = (
        select(func.min(ValorPrestacion.ID).label("min_id"))
        .join(
            fecha_subq,
            (ValorPrestacion.NRO_OBRASOCIAL == fecha_subq.c.nos)
            & (ValorPrestacion.CODIGOS == fecha_subq.c.cod)
            & (ValorPrestacion.FECHA_CAMBIO == fecha_subq.c.max_fecha),
        )
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


@router.get(
    "/galenos",
    response_model=List[ValoresBoletinOut],
    summary="Listar valores galeno por obra social y nivel",
)
async def listar_valores_galenos(
    db: AsyncSession = Depends(get_db),
    nro_obra_social: Optional[int] = Query(None, description="Nro de obra social"),
    nivel: Optional[int] = Query(None, description="Nivel"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=500),
):
    stmt = (
        select(
            ValoresBoletin.ID.label("id"),
            ValoresBoletin.NRO_OBRASOCIAL.label("nro_obrasocial"),
            ObrasSociales.OBRA_SOCIAL.label("obra_social"),
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
        .join(
            ObrasSociales,
            ObrasSociales.NRO_OBRASOCIAL == ValoresBoletin.NRO_OBRASOCIAL,
            isouter=True,
        )
    )

    if nro_obra_social is not None:
        stmt = stmt.where(ValoresBoletin.NRO_OBRASOCIAL == nro_obra_social)
    if nivel is not None:
        stmt = stmt.where(ValoresBoletin.NIVEL == nivel)

    stmt = (
        stmt
        .order_by(ValoresBoletin.NRO_OBRASOCIAL.asc(), ValoresBoletin.NIVEL.asc())
        .offset((page - 1) * size)
        .limit(size)
    )

    rows = (await db.execute(stmt)).mappings().all()
    return [ValoresBoletinOut(**row) for row in rows]
