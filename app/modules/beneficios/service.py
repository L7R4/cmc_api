"""Acceso a datos de beneficios. Las funciones corren dentro de la transacción
del caller (igual que modules/nomenclador/service.py): no hacen commit."""
from __future__ import annotations

import datetime
from typing import Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.beneficios import Beneficio


def _base_query(
    categoria: Optional[str] = None,
    activo: Optional[bool] = None,
    q: Optional[str] = None,
) -> Select:
    stmt = select(Beneficio)
    if categoria:
        stmt = stmt.where(Beneficio.categoria == categoria)
    if activo is not None:
        stmt = stmt.where(Beneficio.activo == activo)
    if q:
        # Escapamos los comodines de LIKE para que el usuario no pueda forzar
        # un full scan con '%%%' ni buscar literales '_' por accidente.
        like = f"%{q.replace('!', '!!').replace('%', '!%').replace('_', '!_')}%"
        stmt = stmt.where(
            or_(
                Beneficio.titulo.like(like, escape="!"),
                Beneficio.ubicacion.like(like, escape="!"),
            )
        )
    return stmt


async def listar(
    db: AsyncSession,
    categoria: Optional[str] = None,
    activo: Optional[bool] = None,
    q: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> Sequence[Beneficio]:
    stmt = (
        _base_query(categoria, activo, q)
        .order_by(Beneficio.categoria.asc(), Beneficio.titulo.asc())
        .offset(skip)
        .limit(limit)
    )
    return (await db.execute(stmt)).scalars().all()


async def contar(
    db: AsyncSession,
    categoria: Optional[str] = None,
    activo: Optional[bool] = None,
    q: Optional[str] = None,
) -> int:
    stmt = _base_query(categoria, activo, q).with_only_columns(
        func.count(Beneficio.id)
    ).order_by(None)
    return int((await db.execute(stmt)).scalar_one() or 0)


async def obtener_o_404(db: AsyncSession, beneficio_id: int) -> Beneficio:
    obj = await db.get(Beneficio, beneficio_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Beneficio no encontrado")
    return obj


async def listar_activos(db: AsyncSession) -> Sequence[Beneficio]:
    """Los que ve la app móvil. Usa el índice (activo, categoria)."""
    stmt = (
        select(Beneficio)
        .where(Beneficio.activo == True)  # noqa: E712
        .order_by(Beneficio.categoria.asc(), Beneficio.titulo.asc())
    )
    return (await db.execute(stmt)).scalars().all()


async def listar_vigentes(
    db: AsyncSession, limit: Optional[int] = None
) -> Sequence[Beneficio]:
    """Los que ve el socio en el portal web (Inicio médico).

    A diferencia de `listar_activos` (móvil), descarta los que ya vencieron: el
    portal es una vidriera y mandar a un socio a un comercio con un descuento
    caído es peor que no mostrarlo. `vigencia_hasta` NULL = sin vencimiento.
    Los vencidos siguen visibles en el ABM del admin, marcados para que los baje.
    """
    hoy = datetime.date.today()
    stmt = (
        select(Beneficio)
        .where(
            Beneficio.activo == True,  # noqa: E712
            or_(Beneficio.vigencia_hasta.is_(None), Beneficio.vigencia_hasta >= hoy),
        )
        .order_by(Beneficio.categoria.asc(), Beneficio.titulo.asc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return (await db.execute(stmt)).scalars().all()
