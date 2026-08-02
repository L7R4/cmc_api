"""Acceso a datos de avisos push. Las funciones corren dentro de la transacción
del caller (igual que modules/beneficios/service.py): no hacen commit."""
from __future__ import annotations

from typing import Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.avisos_push import AvisoPush
from app.db.models.medico import ListadoMedico

# Tope del listado que se le manda al app: es una bandeja de avisos, no un
# archivo histórico. El panel sí pagina sobre todo.
MOBILE_LIMIT = 50


def _base_query(
    tipo: Optional[str] = None,
    activo: Optional[bool] = None,
    q: Optional[str] = None,
) -> Select:
    stmt = select(AvisoPush)
    if tipo:
        stmt = stmt.where(AvisoPush.tipo == tipo)
    if activo is not None:
        stmt = stmt.where(AvisoPush.activo == activo)
    if q:
        # Escapamos los comodines de LIKE para que el usuario no pueda forzar un
        # full scan con '%%%' ni buscar literales '_' por accidente.
        like = f"%{q.replace('!', '!!').replace('%', '!%').replace('_', '!_')}%"
        stmt = stmt.where(
            or_(
                AvisoPush.titulo.like(like, escape="!"),
                AvisoPush.mensaje.like(like, escape="!"),
            )
        )
    return stmt


async def listar(
    db: AsyncSession,
    tipo: Optional[str] = None,
    activo: Optional[bool] = None,
    q: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> Sequence[AvisoPush]:
    stmt = (
        _base_query(tipo, activo, q)
        .order_by(AvisoPush.publicado_at.desc(), AvisoPush.id.desc())
        .offset(skip)
        .limit(limit)
    )
    return (await db.execute(stmt)).scalars().all()


async def contar(
    db: AsyncSession,
    tipo: Optional[str] = None,
    activo: Optional[bool] = None,
    q: Optional[str] = None,
) -> int:
    stmt = (
        _base_query(tipo, activo, q)
        .with_only_columns(func.count(AvisoPush.id))
        .order_by(None)
    )
    return int((await db.execute(stmt)).scalar_one() or 0)


async def obtener_o_404(db: AsyncSession, aviso_id: int) -> AvisoPush:
    obj = await db.get(AvisoPush, aviso_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Aviso no encontrado")
    return obj


async def listar_activos(db: AsyncSession, limit: int = MOBILE_LIMIT) -> Sequence[AvisoPush]:
    """Los que ve la app móvil. Usa el índice (activo, publicado_at)."""
    stmt = (
        select(AvisoPush)
        .where(AvisoPush.activo == True)  # noqa: E712
        .order_by(AvisoPush.publicado_at.desc(), AvisoPush.id.desc())
        .limit(limit)
    )
    return (await db.execute(stmt)).scalars().all()


async def nombres_por_id(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    """NOMBRE de los admins que publicaron avisos, para mostrarlo en el panel.
    Mismo criterio que solicitudes_cambio.service.nombres_por_id."""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = await db.execute(
        select(ListadoMedico.ID, ListadoMedico.NOMBRE).where(ListadoMedico.ID.in_(ids))
    )
    return {int(i): (n or "").strip() for i, n in rows.all()}


async def autor_id(db: AsyncSession, token_user: dict) -> Optional[int]:
    """ListadoMedico.ID del admin logueado, para guardarlo en enviado_por.

    El JWT sólo trae NRO_SOCIO (`sub`), así que hace falta una lookup; es una
    sola columna por índice, no se carga la entidad entera.
    """
    try:
        nro_socio = int(token_user["nro_socio"])
    except (KeyError, TypeError, ValueError):
        return None
    return (
        await db.execute(select(ListadoMedico.ID).where(ListadoMedico.NRO_SOCIO == nro_socio))
    ).scalars().first()
