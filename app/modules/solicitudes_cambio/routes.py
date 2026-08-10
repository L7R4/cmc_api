"""Bandeja de solicitudes de cambio de datos (web admin).

La bandeja entera exige el scope `solicitudes_cambio:gestionar`: acá se leen y
se resuelven. Aparte va `router_socio`, donde el propio médico abre su reclamo
desde el portal web (Mi perfil) y consulta los suyos — sin ese scope, que un
socio no tiene. La app móvil crea los suyos por el BFF
(POST /api/mobile/solicitudes-cambio), con su propio contrato.

Aprobar NO modifica el ListadoMedico — el admin aplica el cambio a mano; por eso
la respuesta siempre expone valor_actual / valor_propuesto.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import (
    get_current_user,
    get_current_user_with_scopes_and_role,
    require_scope,
)
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
    CampoEditableOut,
    ResolverIn,
    SolicitudCambioCounts,
    SolicitudCambioCrearIn,
    SolicitudCambioFormularioIn,
    SolicitudCambioListOut,
    SolicitudCambioMiaOut,
    SolicitudCambioOut,
)

router = APIRouter()  # El scope lo declara app/auth/authz.py::SCOPES_POR_RUTA (fuente unica de autorizacion).

# Sólo autenticación: el socio abre y consulta SUS reclamos, nunca los de otro.
router_socio = APIRouter()

MAX_PAGE_SIZE = 100

# Cuántos reclamos propios devuelve el historial de "Mi perfil".
MAX_MIAS = 50


@router_socio.post(
    "/", response_model=SolicitudCambioMiaOut, status_code=status.HTTP_201_CREATED
)
async def crear_solicitud_propia(
    body: SolicitudCambioCrearIn,
    db: AsyncSession = Depends(get_db),
    dep=Depends(get_current_user_with_scopes_and_role),
):
    """El médico reporta desde el portal web que un dato suyo está mal.

    Queda 'pendiente' para que un admin la resuelva en la bandeja. nro_socio y
    medico_id salen del token — el body no puede elegirlos. Reusa el mismo
    service que el alta móvil, así que comparte el tope de pendientes por médico
    (429) que protege la bandeja de una inundación.
    """
    user, _scopes, _role = dep
    obj = await service.crear_desde_movil(
        db,
        nro_socio=user.NRO_SOCIO,
        medico_id=user.ID,
        campo=body.campo,
        valor_actual=body.valor_actual,
        valor_propuesto=body.valor_propuesto,
        mensaje=body.mensaje,
    )
    await db.commit()
    await db.refresh(obj)
    return SolicitudCambioMiaOut.model_validate(obj, from_attributes=True)


@router_socio.get("/campos-editables", response_model=List[CampoEditableOut])
async def campos_editables(
    db: AsyncSession = Depends(get_db),
    dep=Depends(get_current_user_with_scopes_and_role),
):
    """Los datos que el médico puede pedir corregir, con lo que figura hoy.

    Es lo que llena el formulario del portal: la lista sale del backend y no del
    front, así que agregar o sacar un campo editable es un solo cambio y no hay
    forma de que la pantalla ofrezca algo que después el alta va a descartar.
    """
    user, _scopes, _role = dep
    medico = await db.get(ListadoMedico, user.ID)
    if medico is None:
        raise HTTPException(404, "No se encontró tu legajo.")

    salida = []
    for campo, columna in service.CAMPOS_EDITABLES_POR_MEDICO.items():
        actual = getattr(medico, columna, None)
        salida.append(
            CampoEditableOut(
                campo=campo,
                etiqueta=service.ETIQUETAS_CAMPOS.get(campo, campo),
                valor_actual=None if actual is None else str(actual).strip() or None,
            )
        )
    return salida


@router_socio.post(
    "/formulario",
    response_model=SolicitudCambioMiaOut,
    status_code=status.HTTP_201_CREATED,
)
async def crear_desde_formulario(
    body: SolicitudCambioFormularioIn,
    db: AsyncSession = Depends(get_db),
    dep=Depends(get_current_user_with_scopes_and_role),
):
    """El médico manda su formulario completo y queda UNA solicitud con el diff.

    Sólo se guardan los campos que efectivamente cambiaron, y el "valor actual"
    lo lee el backend de la base. Al aprobarla, los cambios se escriben en el
    legajo automáticamente (ver `service.resolver`).
    """
    user, _scopes, _role = dep
    obj = await service.crear_desde_formulario(
        db,
        nro_socio=user.NRO_SOCIO,
        medico_id=user.ID,
        propuestos=body.valores,
        mensaje=body.mensaje,
    )
    await db.commit()
    await db.refresh(obj)
    return SolicitudCambioMiaOut.model_validate(obj, from_attributes=True)


@router_socio.get("/mias", response_model=List[SolicitudCambioMiaOut])
async def listar_solicitudes_propias(
    db: AsyncSession = Depends(get_db),
    dep=Depends(get_current_user_with_scopes_and_role),
):
    """Los reclamos del socio logueado, del más nuevo al más viejo.

    Filtra por nro_socio del token: no hay forma de pedir los de otro. Permite
    que "Mi perfil" muestre en qué quedó cada reclamo (pendiente/aprobada/
    rechazada) junto con la respuesta del admin.
    """
    user, _scopes, _role = dep
    rows = (
        await db.execute(
            select(SolicitudCambioMedico)
            .where(SolicitudCambioMedico.nro_socio == user.NRO_SOCIO)
            .order_by(SolicitudCambioMedico.created_at.desc())
            .limit(MAX_MIAS)
        )
    ).scalars().all()
    return [
        SolicitudCambioMiaOut.model_validate(r, from_attributes=True) for r in rows
    ]


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
