import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models.catalogs import ObrasSociales
from app.db.models.nomenclador_cmc import HistorialPrecioCodigo, NomencladorCMC, Valor
from app.modules.nomenclador.schemas import (
    BoletinItemOut,
    BoletinOut,
    EvolucionPrecioItem,
    RankingItem,
    RankingValoresOut,
    TablaValoresItem,
)

router = APIRouter()


def _componentes_from_snapshot(snapshot: list) -> list:
    from app.modules.nomenclador.schemas import BoletinComponenteOut
    out = []
    for item in snapshot:
        out.append(BoletinComponenteOut(
            concepto=item["concepto"],
            tipo=item["tipo"],
            valor_unitario=Decimal(item["valor_unitario"]) if item.get("valor_unitario") else None,
            cantidad=Decimal(item["cantidad"]) if item.get("cantidad") else None,
            subtotal=Decimal(item["subtotal"]),
        ))
    return out


async def _nombre_os(obra_social_nro: int, db: AsyncSession) -> str:
    stmt = select(ObrasSociales).where(ObrasSociales.NRO_OBRASOCIAL == obra_social_nro)
    os = (await db.execute(stmt)).scalar_one_or_none()
    return os.OBRA_SOCIAL if os else str(obra_social_nro)


@router.get("/ranking_valores", response_model=RankingValoresOut)
async def ranking_valores(
    fecha_referencia: Optional[datetime.date] = Query(None),
    codigo: str = Query("420101", description="Código CMC de consulta a comparar"),
    db: AsyncSession = Depends(get_db),
):
    """Lista todas las OS con su valor del código dado, ordenadas de mayor a menor."""
    fecha = fecha_referencia or datetime.date.today()

    # Buscar el nomenclador por código
    stmt_nom = select(NomencladorCMC).where(
        NomencladorCMC.codigo == codigo, NomencladorCMC.activo == True
    )
    nom = (await db.execute(stmt_nom)).scalar_one_or_none()
    if not nom:
        raise HTTPException(404, f"Código '{codigo}' no encontrado en el nomenclador")

    stmt = select(HistorialPrecioCodigo).where(
        HistorialPrecioCodigo.nomenclador_id == nom.id,
        HistorialPrecioCodigo.vigencia_desde <= fecha,
        (HistorialPrecioCodigo.vigencia_hasta.is_(None))
        | (HistorialPrecioCodigo.vigencia_hasta >= fecha),
    ).order_by(HistorialPrecioCodigo.precio_total.desc())
    result = await db.execute(stmt)
    historiales = result.scalars().all()

    ranking = []
    for pos, h in enumerate(historiales, start=1):
        nombre = await _nombre_os(h.obra_social_nro, db)
        ranking.append(RankingItem(
            posicion=pos,
            obra_social_nro=h.obra_social_nro,
            nombre_os=nombre,
            valor=h.precio_total,
        ))

    return RankingValoresOut(
        fecha_referencia=fecha,
        codigo_consulta=codigo,
        ranking=ranking,
    )


@router.get("/boletin", response_model=BoletinOut)
async def boletin(
    fecha: datetime.date = Query(...),
    obra_social_nro: Optional[int] = Query(None),
    codigo: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Consulta detallada de valores por OS y/o código a una fecha."""
    stmt = select(HistorialPrecioCodigo).where(
        HistorialPrecioCodigo.vigencia_desde <= fecha,
        (HistorialPrecioCodigo.vigencia_hasta.is_(None))
        | (HistorialPrecioCodigo.vigencia_hasta >= fecha),
    )
    if obra_social_nro:
        stmt = stmt.where(HistorialPrecioCodigo.obra_social_nro == obra_social_nro)

    if codigo:
        stmt_nom = select(NomencladorCMC).where(NomencladorCMC.codigo == codigo)
        nom = (await db.execute(stmt_nom)).scalar_one_or_none()
        if not nom:
            raise HTTPException(404, f"Código '{codigo}' no encontrado")
        stmt = stmt.where(HistorialPrecioCodigo.nomenclador_id == nom.id)

    stmt = stmt.order_by(HistorialPrecioCodigo.obra_social_nro, HistorialPrecioCodigo.nomenclador_id)
    result = await db.execute(stmt)
    historiales = result.scalars().all()

    items = []
    for h in historiales:
        nom = await db.get(NomencladorCMC, h.nomenclador_id)
        valor = await db.get(Valor, h.valores_id)
        items.append(BoletinItemOut(
            codigo=nom.codigo if nom else str(h.nomenclador_id),
            origen=h.origen,
            descripcion=valor.descripcion if valor else None,
            nivel=valor.nivel if valor else None,
            por_presupuesto=bool(valor and valor.por_presupuesto),
            precio_total=h.precio_total,
            componentes=_componentes_from_snapshot(h.componentes_snapshot),
            vigencia_desde=h.vigencia_desde,
            vigencia_hasta=h.vigencia_hasta,
        ))

    return BoletinOut(fecha=fecha, obra_social_nro=obra_social_nro, items=items)


@router.get("/tabla_valores", response_model=List[TablaValoresItem])
async def tabla_valores(
    obra_social_nro: int = Query(...),
    fecha: Optional[datetime.date] = Query(None),
    codigo: Optional[str] = Query(None),
    orden: str = Query("codigo", pattern="^(codigo|valor)$"),
    page: int = Query(1, ge=1),
    size: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Todos los códigos vigentes de una OS con su precio a una fecha."""
    fecha_ref = fecha or datetime.date.today()

    stmt = select(HistorialPrecioCodigo).where(
        HistorialPrecioCodigo.obra_social_nro == obra_social_nro,
        HistorialPrecioCodigo.vigencia_desde <= fecha_ref,
        (HistorialPrecioCodigo.vigencia_hasta.is_(None))
        | (HistorialPrecioCodigo.vigencia_hasta >= fecha_ref),
    )
    if codigo:
        stmt_nom = select(NomencladorCMC).where(NomencladorCMC.codigo == codigo)
        nom = (await db.execute(stmt_nom)).scalar_one_or_none()
        if nom:
            stmt = stmt.where(HistorialPrecioCodigo.nomenclador_id == nom.id)

    if orden == "valor":
        stmt = stmt.order_by(HistorialPrecioCodigo.precio_total.desc())
    else:
        stmt = stmt.join(NomencladorCMC, NomencladorCMC.id == HistorialPrecioCodigo.nomenclador_id).order_by(NomencladorCMC.codigo)

    stmt = stmt.offset((page - 1) * size).limit(size)
    result = await db.execute(stmt)
    historiales = result.scalars().all()

    items = []
    for h in historiales:
        nom = await db.get(NomencladorCMC, h.nomenclador_id)
        valor = await db.get(Valor, h.valores_id)
        items.append(TablaValoresItem(
            nomenclador_id=h.nomenclador_id,
            codigo=nom.codigo if nom else str(h.nomenclador_id),
            origen=h.origen,
            descripcion=valor.descripcion if valor else None,
            nivel=valor.nivel if valor else None,
            por_presupuesto=bool(valor and valor.por_presupuesto),
            precio_total=h.precio_total,
            vigencia_desde=h.vigencia_desde,
            vigencia_hasta=h.vigencia_hasta,
            componentes=h.componentes_snapshot,
        ))
    return items


@router.get("/evolucion_precios", response_model=List[EvolucionPrecioItem])
async def evolucion_precios(
    nomenclador_id: int = Query(...),
    obra_social_nro: int = Query(...),
    desde: Optional[datetime.date] = Query(None),
    hasta: Optional[datetime.date] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Serie temporal del precio de un código para una OS."""
    stmt = select(HistorialPrecioCodigo).where(
        HistorialPrecioCodigo.nomenclador_id == nomenclador_id,
        HistorialPrecioCodigo.obra_social_nro == obra_social_nro,
    )
    if desde:
        stmt = stmt.where(HistorialPrecioCodigo.vigencia_desde >= desde)
    if hasta:
        stmt = stmt.where(HistorialPrecioCodigo.vigencia_desde <= hasta)
    stmt = stmt.order_by(HistorialPrecioCodigo.vigencia_desde)
    result = await db.execute(stmt)
    historiales = result.scalars().all()

    return [
        EvolucionPrecioItem(
            vigencia_desde=h.vigencia_desde,
            vigencia_hasta=h.vigencia_hasta,
            precio_total=h.precio_total,
            motivo_cambio=h.motivo_cambio,
            fecha_cambio=h.fecha_cambio,
        )
        for h in historiales
    ]
