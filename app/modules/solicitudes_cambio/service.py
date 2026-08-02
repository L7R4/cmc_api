"""Lógica de las solicitudes de cambio de datos.

Las funciones de lectura no mutan; `resolver()` deja el commit al caller
(mismo criterio que modules/nomenclador/service.py).
"""
from __future__ import annotations

import datetime
from typing import Dict, Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.medico import ListadoMedico
from app.db.models.solicitud_cambio import (
    ESTADOS_SOLICITUD_CAMBIO,
    SolicitudCambioMedico,
)

# Tope de solicitudes pendientes por médico. Evita que un cliente comprometido
# (o un doble-tap en el app) inunde la bandeja del admin.
MAX_PENDIENTES_POR_MEDICO = 10


async def contar_por_estado(db: AsyncSession) -> Dict[str, int]:
    """Un solo GROUP BY para los cuatro contadores de los badges."""
    rows = (
        await db.execute(
            select(SolicitudCambioMedico.estado, func.count(SolicitudCambioMedico.id))
            .group_by(SolicitudCambioMedico.estado)
        )
    ).all()
    counts = {estado: 0 for estado in ESTADOS_SOLICITUD_CAMBIO}
    for estado, cantidad in rows:
        counts[str(estado)] = int(cantidad)
    counts["total"] = sum(counts[e] for e in ESTADOS_SOLICITUD_CAMBIO)
    return counts


async def listar(
    db: AsyncSession,
    estado: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[Sequence[tuple[SolicitudCambioMedico, Optional[str]]], int]:
    """(filas, total). Cada fila es (solicitud, nombre del médico).

    El nombre sale de un LEFT JOIN por ListadoMedico.ID (PK, no duplica filas);
    si el médico fue borrado, medico_id queda NULL y el nombre viene vacío —
    nro_socio se conserva igual en la solicitud.
    """
    stmt = (
        select(SolicitudCambioMedico, ListadoMedico.NOMBRE)
        .outerjoin(ListadoMedico, ListadoMedico.ID == SolicitudCambioMedico.medico_id)
    )
    count_stmt = select(func.count(SolicitudCambioMedico.id))
    if estado:
        stmt = stmt.where(SolicitudCambioMedico.estado == estado)
        count_stmt = count_stmt.where(SolicitudCambioMedico.estado == estado)

    stmt = (
        stmt.order_by(SolicitudCambioMedico.created_at.desc(), SolicitudCambioMedico.id.desc())
        .offset(skip)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    total = int((await db.execute(count_stmt)).scalar_one() or 0)
    return [(r[0], r[1]) for r in rows], total


async def obtener_o_404(
    db: AsyncSession, solicitud_id: int
) -> tuple[SolicitudCambioMedico, Optional[str]]:
    row = (
        await db.execute(
            select(SolicitudCambioMedico, ListadoMedico.NOMBRE)
            .outerjoin(ListadoMedico, ListadoMedico.ID == SolicitudCambioMedico.medico_id)
            .where(SolicitudCambioMedico.id == solicitud_id)
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    return row[0], row[1]


async def nombres_por_id(db: AsyncSession, ids: set[int]) -> Dict[int, str]:
    """Resuelve varios ListadoMedico.ID → NOMBRE en una query (para revisado_por)."""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(ListadoMedico.ID, ListadoMedico.NOMBRE).where(ListadoMedico.ID.in_(ids))
        )
    ).all()
    return {int(i): (n or "") for i, n in rows}


async def resolver(
    db: AsyncSession,
    solicitud_id: int,
    nuevo_estado: str,
    revisado_por: Optional[int],
    respuesta_admin: Optional[str],
) -> SolicitudCambioMedico:
    """Aprueba o rechaza. NO edita el ListadoMedico: el admin aplica el cambio
    a mano usando valor_propuesto. Sólo se puede resolver una vez."""
    if nuevo_estado not in ("aprobada", "rechazada"):
        raise HTTPException(status_code=422, detail="Estado destino inválido")

    obj = await db.get(SolicitudCambioMedico, solicitud_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    if obj.estado != "pendiente":
        raise HTTPException(
            status_code=409, detail=f"La solicitud ya fue resuelta (estado={obj.estado})"
        )

    obj.estado = nuevo_estado
    obj.revisado_por = revisado_por
    obj.revisado_at = datetime.datetime.now()
    obj.respuesta_admin = respuesta_admin
    return obj


async def crear_desde_movil(
    db: AsyncSession,
    nro_socio: int,
    medico_id: Optional[int],
    campo: str,
    valor_actual: Optional[str],
    valor_propuesto: Optional[str],
    mensaje: str,
) -> SolicitudCambioMedico:
    """Alta desde la app. nro_socio/medico_id vienen del token, nunca del body."""
    pendientes = int(
        (
            await db.execute(
                select(func.count(SolicitudCambioMedico.id)).where(
                    SolicitudCambioMedico.nro_socio == nro_socio,
                    SolicitudCambioMedico.estado == "pendiente",
                )
            )
        ).scalar_one()
        or 0
    )
    if pendientes >= MAX_PENDIENTES_POR_MEDICO:
        raise HTTPException(
            status_code=429,
            detail="Ya tenés varias solicitudes pendientes. Esperá a que las revisen.",
        )

    obj = SolicitudCambioMedico(
        nro_socio=nro_socio,
        medico_id=medico_id,
        campo=campo,
        valor_actual=valor_actual,
        valor_propuesto=valor_propuesto,
        mensaje=mensaje,
        estado="pendiente",
    )
    db.add(obj)
    return obj
