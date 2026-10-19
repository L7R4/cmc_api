"""Acceso a datos de tareas_dia. Corre dentro de la transacción del caller: no
hace commit (igual que modules/avisos/service.py).

Toda función recibe `medico_id` del token y lo mete en el WHERE: no existe
ninguna consulta que busque una tarea sin filtrar por dueño, así que una tarea
ajena es indistinguible de una que no existe (404, nunca 403).
"""
from __future__ import annotations

import datetime
from typing import Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.medico import ListadoMedico
from app.db.models.tareas_dia import TAREAS_MAX, TareaDia

# Argentina no usa horario de verano: un offset fijo evita depender de tzdata,
# que la imagen de producción no trae.
_ARGENTINA = datetime.timezone(datetime.timedelta(hours=-3))


def hoy() -> datetime.date:
    """El día de hoy en hora argentina (el servidor corre en UTC)."""
    return datetime.datetime.now(_ARGENTINA).date()


async def _serializar(db: AsyncSession, medico_id: int) -> None:
    """Bloquea la fila del usuario hasta el commit.

    Dos pestañas (o dos requests de volcado) del mismo usuario no pueden contar
    ni insertar a la vez: sin esto ambas ven "hay lugar" y pasan del tope, o el
    volcado duplica. Es una fila por PK; no bloquea a otros usuarios.
    """
    await db.execute(
        select(ListadoMedico.ID).where(ListadoMedico.ID == medico_id).with_for_update()
    )


def _visibles(medico_id: int):
    """Pendientes, más las que se tildaron hoy."""
    return and_(
        TareaDia.medico_id == medico_id,
        or_(TareaDia.hecha.is_(False), TareaDia.hecha_el >= hoy()),
    )


async def listar(db: AsyncSession, medico_id: int) -> Sequence[TareaDia]:
    q = select(TareaDia).where(_visibles(medico_id)).order_by(TareaDia.id)
    return (await db.execute(q)).scalars().all()


async def _purgar_vencidas(db: AsyncSession, medico_id: int) -> None:
    """Borra las tildadas en días anteriores (ya no se ven)."""
    await db.execute(
        delete(TareaDia).where(
            TareaDia.medico_id == medico_id,
            TareaDia.hecha.is_(True),
            TareaDia.hecha_el < hoy(),
        )
    )


async def obtener_o_404(db: AsyncSession, medico_id: int, tarea_id: int) -> TareaDia:
    obj = (
        await db.execute(
            select(TareaDia).where(TareaDia.id == tarea_id, TareaDia.medico_id == medico_id)
        )
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(404, "Tarea no encontrada")
    return obj


async def crear(db: AsyncSession, medico_id: int, texto: str, prioridad: str) -> TareaDia:
    await _serializar(db, medico_id)
    await _purgar_vencidas(db, medico_id)
    if len(await listar(db, medico_id)) >= TAREAS_MAX:
        raise HTTPException(409, f"El cuaderno admite hasta {TAREAS_MAX} tareas")
    obj = TareaDia(medico_id=medico_id, texto=texto, prioridad=prioridad)
    db.add(obj)
    await db.flush()
    return obj


async def actualizar(
    db: AsyncSession,
    medico_id: int,
    tarea_id: int,
    *,
    texto: Optional[str],
    hecha: Optional[bool],
    prioridad: Optional[str],
) -> TareaDia:
    obj = await obtener_o_404(db, medico_id, tarea_id)
    if texto is not None:
        obj.texto = texto
    if prioridad is not None:
        obj.prioridad = prioridad
    if hecha is not None and hecha != obj.hecha:
        obj.hecha = hecha
        obj.hecha_el = hoy() if hecha else None
    await db.flush()
    return obj


async def quitar(db: AsyncSession, medico_id: int, tarea_id: int) -> None:
    obj = await obtener_o_404(db, medico_id, tarea_id)
    await db.delete(obj)


async def limpiar_hechas(db: AsyncSession, medico_id: int) -> None:
    await db.execute(
        delete(TareaDia).where(TareaDia.medico_id == medico_id, TareaDia.hecha.is_(True))
    )


async def importar(db: AsyncSession, medico_id: int, tareas: list) -> Sequence[TareaDia]:
    """Vuelca lo que el usuario tenía en su navegador. Idempotente.

    Una tarea con el mismo texto que otra ya guardada no se vuelve a crear: así
    repetir el volcado (otra pestaña, un reintento tras un corte) no duplica.
    Si no entra todo, se importa lo que cabe y se descarta el resto.
    """
    await _serializar(db, medico_id)
    await _purgar_vencidas(db, medico_id)
    existentes = list(await listar(db, medico_id))
    vistos = {t.texto.casefold() for t in existentes}
    lugar = TAREAS_MAX - len(existentes)
    dia = hoy()
    for t in tareas:
        if lugar <= 0:
            break
        clave = t.texto.casefold()
        if clave in vistos:
            continue
        vistos.add(clave)
        db.add(
            TareaDia(
                medico_id=medico_id,
                texto=t.texto,
                hecha=t.hecha,
                prioridad=t.prioridad,
                hecha_el=dia if t.hecha else None,
            )
        )
        lugar -= 1
    await db.flush()
    return await listar(db, medico_id)
