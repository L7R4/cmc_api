from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import collate, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.database import get_db
from app.db.models import Codigoprestacionswiss, ObrasSociales, ValorNomencladoSwiss, ValorPrestacion, ValoresBoletin
from app.modules.catalogs.schemas import ValorNomencladoSwissOut, ValorNomencladoSwissUpdate, ValorPrestacionIn, ValorPrestacionOut, ValoresBoletinOut

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
            ValorPrestacion.FECHA_FINAL.label("fecha_final"),
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


@router.post(
    "/boletin",
    response_model=ValorPrestacionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Insertar nuevo valor de prestación (boletin)",
)
async def crear_valor_boletin(
    body: ValorPrestacionIn,
    db: AsyncSession = Depends(get_db),
):
    row = ValorPrestacion(
        CODIGOS=body.codigos,
        NRO_OBRASOCIAL=body.nro_obrasocial,
        HONORARIOS_A=body.honorarios_a,
        HONORARIOS_B=body.honorarios_b,
        HONORARIOS_C=body.honorarios_c,
        GASTOS=body.gastos,
        AYUDANTE_A=body.ayudante_a,
        AYUDANTE_B=body.ayudante_b,
        AYUDANTE_C=body.ayudante_c,
        C_P_H_S=body.c_p_h_s or "",
        FECHA_CAMBIO=body.fecha_cambio,
        FECHA_FINAL=body.fecha_final,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    obra_social_name: str | None = None
    if body.obra_social:
        obra_social_name = body.obra_social
    else:
        os_row = await db.get(ObrasSociales, row.NRO_OBRASOCIAL)
        if os_row:
            obra_social_name = os_row.OBRA_SOCIAL

    return ValorPrestacionOut(
        id=row.ID,
        codigos=row.CODIGOS,
        nro_obrasocial=row.NRO_OBRASOCIAL,
        obra_social=obra_social_name,
        honorarios_a=row.HONORARIOS_A,
        honorarios_b=row.HONORARIOS_B,
        honorarios_c=row.HONORARIOS_C,
        gastos=row.GASTOS,
        ayudante_a=row.AYUDANTE_A,
        ayudante_b=row.AYUDANTE_B,
        ayudante_c=row.AYUDANTE_C,
        c_p_h_s=row.C_P_H_S,
        fecha_cambio=row.FECHA_CAMBIO,
        fecha_final=row.FECHA_FINAL,
    )


@router.delete(
    "/boletin/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar un valor de prestación por ID",
)
async def eliminar_valor_boletin(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(ValorPrestacion, id)
    if row is None:
        raise HTTPException(status_code=404, detail="Valor no encontrado")
    await db.delete(row)
    await db.commit()


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


@router.get(
    "/nomenclado-swiss",
    response_model=List[ValorNomencladoSwissOut],
    summary="Listar valores del nomenclador Swiss",
)
async def listar_nomenclado_swiss(
    db: AsyncSession = Depends(get_db),
    codigo: Optional[str] = Query(None, description="Código de prestación"),
    c_p_h_s: Optional[str] = Query(None, description="Tipo C/P/H/S"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=500),
    _=Depends(get_current_user),
):
    stmt = (
        select(
            ValorNomencladoSwiss.ID.label("id"),
            ValorNomencladoSwiss.CODIGO.label("codigo"),
            ValorNomencladoSwiss.C_P_H_S.label("c_p_h_s"),
            Codigoprestacionswiss.DESCRIPCION.label("descripcion"),
            ValorNomencladoSwiss.HONORARIOS_A.label("honorarios_a"),
            ValorNomencladoSwiss.GASTOS.label("gastos"),
            ValorNomencladoSwiss.AYUDANTE_A.label("ayudante_a"),
        )
        .select_from(ValorNomencladoSwiss)
        .join(
            Codigoprestacionswiss,
            collate(Codigoprestacionswiss.CODIGO, "utf8_general_ci") == ValorNomencladoSwiss.CODIGO,
            isouter=True,
        )
    )

    if codigo is not None:
        stmt = stmt.where(ValorNomencladoSwiss.CODIGO == codigo)
    if c_p_h_s is not None:
        stmt = stmt.where(ValorNomencladoSwiss.C_P_H_S == c_p_h_s)

    stmt = stmt.order_by(ValorNomencladoSwiss.CODIGO.asc()).offset((page - 1) * size).limit(size)

    rows = (await db.execute(stmt)).mappings().all()
    return [ValorNomencladoSwissOut(**row) for row in rows]


@router.patch(
    "/nomenclado-swiss/{id}",
    response_model=ValorNomencladoSwissOut,
    summary="Actualizar precios de un valor nomenclado Swiss",
)
async def actualizar_nomenclado_swiss(
    id: int,
    body: ValorNomencladoSwissUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    row = await db.get(ValorNomencladoSwiss, id)
    if row is None:
        raise HTTPException(status_code=404, detail="Valor no encontrado")

    if body.honorarios_a is not None:
        row.HONORARIOS_A = body.honorarios_a
    if body.gastos is not None:
        row.GASTOS = body.gastos
    if body.ayudante_a is not None:
        row.AYUDANTE_A = body.ayudante_a

    await db.commit()
    await db.refresh(row)

    desc_row = await db.execute(
        select(Codigoprestacionswiss.DESCRIPCION).where(Codigoprestacionswiss.CODIGO == row.CODIGO)
    )
    descripcion = desc_row.scalar_one_or_none()

    return ValorNomencladoSwissOut(
        id=row.ID,
        codigo=row.CODIGO,
        c_p_h_s=row.C_P_H_S,
        descripcion=descripcion,
        honorarios_a=row.HONORARIOS_A,
        gastos=row.GASTOS,
        ayudante_a=row.AYUDANTE_A,
    )


@router.delete(
    "/nomenclado-swiss/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar un valor nomenclado Swiss",
)
async def eliminar_nomenclado_swiss(
    id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    row = await db.get(ValorNomencladoSwiss, id)
    if row is None:
        raise HTTPException(status_code=404, detail="Valor no encontrado")
    await db.delete(row)
    await db.commit()
