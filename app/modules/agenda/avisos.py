"""Avisos del día para los administradores del Colegio.

Los reciben quienes tienen el rol `admin` y NINGÚN rol `medico`: un socio nunca
recibe un aviso de la agenda interna, aunque alguien le haya dado también el rol
de administrador.

Argentina no usa horario de verano: un offset fijo evita depender de tzdata,
que la imagen de producción no trae.
"""
import datetime
from typing import List, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgendaEvento, ListadoMedico, Role, UserRole
from app.db.models.agenda_avisos import AgendaAviso
from app.db.models.medico import ESTADO_ACTIVO
from app.modules.agenda.ocurrencias import ocurrencias

ROL_DESTINO = "admin"
ROL_EXCLUIDO = "medico"

_ARGENTINA = datetime.timezone(datetime.timedelta(hours=-3))


def hoy() -> datetime.date:
    """El día de hoy en hora argentina (el servidor corre en UTC)."""
    return datetime.datetime.now(_ARGENTINA).date()


async def destinatarios(db: AsyncSession) -> List[int]:
    """Ids de los administradores activos que no son médicos."""
    son_medicos = select(UserRole.user_id).join(Role, Role.id == UserRole.role_id).where(
        Role.name == ROL_EXCLUIDO
    )
    stmt = (
        select(ListadoMedico.ID)
        .join(UserRole, UserRole.user_id == ListadoMedico.ID)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            Role.name == ROL_DESTINO,
            ListadoMedico.estado_cuenta == ESTADO_ACTIVO,
            ListadoMedico.ID.not_in(son_medicos),
        )
        .distinct()
    )
    return [fila[0] for fila in (await db.execute(stmt)).all()]


async def eventos_de(db: AsyncSession, dia: datetime.date) -> List[AgendaEvento]:
    """Los eventos activos que caen ese día, ya resueltas sus recurrencias."""
    stmt = select(AgendaEvento).where(AgendaEvento.activo.is_(True))
    eventos = (await db.execute(stmt)).scalars().all()
    return [e for e in eventos if dia in ocurrencias(e, dia.year, dia.month)]


async def _contar(db: AsyncSession, dia: datetime.date) -> int:
    stmt = select(func.count()).select_from(AgendaAviso).where(AgendaAviso.fecha == dia)
    return (await db.execute(stmt)).scalar_one()


async def generar_de_hoy(db: AsyncSession) -> Tuple[int, int, int]:
    """Crea el aviso de cada evento de hoy para cada administrador.

    Devuelve (eventos, destinatarios, avisos nuevos). Es idempotente: el índice
    único descarta lo que ya existía, así que se puede correr cuantas veces haga
    falta el mismo día.
    """
    dia = hoy()
    eventos = await eventos_de(db, dia)
    ids = await destinatarios(db)
    if not eventos or not ids:
        return len(eventos), len(ids), 0

    filas = [
        {"medico_id": uid, "evento_id": e.id, "fecha": dia}
        for uid in ids
        for e in eventos
    ]
    antes = await _contar(db, dia)
    await db.execute(mysql_insert(AgendaAviso).prefix_with("IGNORE"), filas)
    await db.commit()
    return len(eventos), len(ids), await _contar(db, dia) - antes


async def pendientes(db: AsyncSession, medico_id: int) -> List[Tuple[AgendaAviso, AgendaEvento]]:
    """Los avisos de hoy sin leer de este usuario."""
    stmt = (
        select(AgendaAviso, AgendaEvento)
        .join(AgendaEvento, AgendaEvento.id == AgendaAviso.evento_id)
        .where(
            AgendaAviso.medico_id == medico_id,
            AgendaAviso.fecha == hoy(),
            AgendaAviso.leido.is_(False),
        )
        .order_by(AgendaEvento.tipo, AgendaEvento.titulo)
    )
    return [(a, e) for a, e in (await db.execute(stmt)).all()]


async def marcar_leidos(db: AsyncSession, medico_id: int) -> None:
    await db.execute(
        update(AgendaAviso)
        .where(
            AgendaAviso.medico_id == medico_id,
            AgendaAviso.fecha == hoy(),
            AgendaAviso.leido.is_(False),
        )
        .values(leido=True)
    )
    await db.commit()
