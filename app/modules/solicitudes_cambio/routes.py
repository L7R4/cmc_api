"""Bandeja de solicitudes de cambio de datos (web admin).

Todo el módulo exige el scope `solicitudes_cambio:gestionar`. Las solicitudes
las crea la app móvil (POST /api/mobile/solicitudes-cambio); acá sólo se leen y
se resuelven.

Aprobar NO modifica el ListadoMedico — el admin aplica el cambio a mano; por eso
la respuesta siempre expone valor_actual / valor_propuesto.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.database import get_db
from app.db.models.medico import ListadoMedico
from app.db.models.solicitud_cambio import (
    CAMPOS_CONOCIDOS,
    ESTADOS_SOLICITUD_CAMBIO,
    SolicitudCambioMedico,
)
from app.modules.solicitudes_cambio import service
from app.modules.solicitudes_cambio.schemas import (
    ResolverIn,
    SolicitudCambioCounts,
    SolicitudCambioListOut,
    SolicitudCambioOut,
)

router = APIRouter()  # El scope lo declara app/auth/authz.py::SCOPES_POR_RUTA (fuente unica de autorizacion).

MAX_PAGE_SIZE = 100


async def _revisor_id(db: AsyncSession, token_user: dict) -> Optional[int]:
    """ListadoMedico.ID del admin logueado, para guardarlo en revisado_por.

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


def _to_out(
    obj: SolicitudCambioMedico,
    medico_nombre: Optional[str],
    revisor_nombre: Optional[str] = None,
) -> SolicitudCambioOut:
    return SolicitudCambioOut(
        id=obj.id,
        nro_socio=obj.nro_socio,
        medico_id=obj.medico_id,
        medico_nombre=(medico_nombre or None),
        campo=obj.campo,
        valor_actual=obj.valor_actual,
        valor_propuesto=obj.valor_propuesto,
        mensaje=obj.mensaje,
        estado=obj.estado,
        revisado_por=obj.revisado_por,
        revisado_por_nombre=(revisor_nombre or None),
        revisado_at=obj.revisado_at,
        respuesta_admin=obj.respuesta_admin,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
    )


@router.get("/campos", response_model=list[str])
async def listar_campos():
    """Vocabulario conocido de `campo` — para los filtros/etiquetas del panel.
    La columna acepta otros valores, esto es sólo lo que ofrece la app."""
    return list(CAMPOS_CONOCIDOS)


@router.get("/", response_model=SolicitudCambioListOut)
async def listar_solicitudes_cambio(
    estado: Optional[str] = Query(None, description="pendiente | aprobada | rechazada"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    db: AsyncSession = Depends(get_db),
):
    """Bandeja paginada + contadores por estado (los contadores son globales,
    no dependen del filtro: alimentan los badges de las pestañas)."""
    if estado is not None and estado not in ESTADOS_SOLICITUD_CAMBIO:
        raise HTTPException(status_code=422, detail="Estado inválido")

    filas, total = await service.listar(db, estado=estado, skip=skip, limit=limit)
    counts = await service.contar_por_estado(db)

    revisores = await service.nombres_por_id(
        db, {s.revisado_por for s, _ in filas if s.revisado_por}
    )
    return SolicitudCambioListOut(
        items=[
            _to_out(s, nombre, revisores.get(s.revisado_por) if s.revisado_por else None)
            for s, nombre in filas
        ],
        total=total,
        counts=SolicitudCambioCounts(**{k: counts[k] for k in ("total", *ESTADOS_SOLICITUD_CAMBIO)}),
    )


@router.get("/{solicitud_id}", response_model=SolicitudCambioOut)
async def obtener_solicitud_cambio(
    solicitud_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    obj, medico_nombre = await service.obtener_o_404(db, solicitud_id)
    revisores = await service.nombres_por_id(db, {obj.revisado_por} if obj.revisado_por else set())
    return _to_out(obj, medico_nombre, revisores.get(obj.revisado_por or 0))


@router.post("/{solicitud_id}/approve", response_model=SolicitudCambioOut)
async def aprobar_solicitud_cambio(
    body: ResolverIn | None = None,
    solicitud_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    token_user: dict = Depends(get_current_user),
):
    """Marca la solicitud como aprobada. No toca el registro del médico."""
    revisor = await _revisor_id(db, token_user)
    obj = await service.resolver(
        db,
        solicitud_id,
        "aprobada",
        revisado_por=revisor,
        respuesta_admin=(body.respuesta_admin if body else None),
    )
    await db.commit()
    _, medico_nombre = await service.obtener_o_404(db, solicitud_id)
    revisores = await service.nombres_por_id(db, {revisor} if revisor else set())
    return _to_out(obj, medico_nombre, revisores.get(revisor or 0))


@router.post("/{solicitud_id}/reject", response_model=SolicitudCambioOut)
async def rechazar_solicitud_cambio(
    body: ResolverIn,
    solicitud_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    token_user: dict = Depends(get_current_user),
):
    """Rechaza la solicitud. El motivo es obligatorio: se lo mostramos al médico."""
    if not body.respuesta_admin:
        raise HTTPException(status_code=422, detail="Indicá el motivo del rechazo")

    revisor = await _revisor_id(db, token_user)
    obj = await service.resolver(
        db,
        solicitud_id,
        "rechazada",
        revisado_por=revisor,
        respuesta_admin=body.respuesta_admin,
    )
    await db.commit()
    _, medico_nombre = await service.obtener_o_404(db, solicitud_id)
    revisores = await service.nombres_por_id(db, {revisor} if revisor else set())
    return _to_out(obj, medico_nombre, revisores.get(revisor or 0))
