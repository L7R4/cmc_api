from datetime import datetime, timedelta, UTC
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_scope
from app.db.database import get_db
from app.db.models import AuditLog, ListadoMedico
from app.modules.auditoria.schemas import AuditLogDetail, AuditLogListItem, PurgeResult

router = APIRouter()

_PURGE_BATCH_SIZE = 1000


@router.get("/", response_model=List[AuditLogListItem], dependencies=[Depends(require_scope("auditoria:ver"))])
async def listar_auditoria(
    nro_socio: Optional[int] = None,
    route: Optional[str] = None,
    method: Optional[str] = None,
    status_code: Optional[int] = None,
    solo_errores: bool = False,
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    A, M = AuditLog, ListadoMedico

    stmt = select(A, M.NOMBRE).outerjoin(M, M.NRO_SOCIO == A.nro_socio)

    if nro_socio is not None:
        stmt = stmt.where(A.nro_socio == nro_socio)
    if route:
        stmt = stmt.where(A.route.ilike(f"%{route}%"))
    if method:
        stmt = stmt.where(A.method == method.upper())
    if status_code is not None:
        stmt = stmt.where(A.status_code == status_code)
    if solo_errores:
        stmt = stmt.where(A.status_code >= 400)
    if desde:
        stmt = stmt.where(A.timestamp >= desde)
    if hasta:
        stmt = stmt.where(A.timestamp <= hasta)

    stmt = stmt.order_by(A.timestamp.desc()).offset(skip).limit(limit)
    rows = (await db.execute(stmt)).all()

    return [
        AuditLogListItem(
            id=log.id,
            timestamp=log.timestamp,
            method=log.method,
            path=log.path,
            route=log.route,
            nro_socio=log.nro_socio,
            nombre_medico=nombre,
            role=log.role,
            status_code=log.status_code,
            duration_ms=log.duration_ms,
            ip=log.ip,
            error_detail=log.error_detail,
        )
        for log, nombre in rows
    ]


@router.get("/{audit_id}", response_model=AuditLogDetail, dependencies=[Depends(require_scope("auditoria:ver"))])
async def obtener_auditoria(audit_id: int, db: AsyncSession = Depends(get_db)):
    log = await db.get(AuditLog, audit_id)
    if not log:
        raise HTTPException(404, "Registro de auditoría no encontrado")

    nombre_medico = None
    if log.nro_socio is not None:
        nombre_medico = (await db.execute(
            select(ListadoMedico.NOMBRE).where(ListadoMedico.NRO_SOCIO == log.nro_socio)
        )).scalar_one_or_none()

    return AuditLogDetail(
        id=log.id,
        timestamp=log.timestamp,
        method=log.method,
        path=log.path,
        route=log.route,
        nro_socio=log.nro_socio,
        nombre_medico=nombre_medico,
        role=log.role,
        status_code=log.status_code,
        duration_ms=log.duration_ms,
        ip=log.ip,
        error_detail=log.error_detail,
        query_params=log.query_params,
        user_agent=log.user_agent,
        request_body=log.request_body,
        request_id=log.request_id,
    )


@router.delete("/purge", response_model=PurgeResult, dependencies=[Depends(require_scope("auditoria:purgar"))])
async def purgar_auditoria(
    months: int = Query(12, ge=1, le=120),
    db: AsyncSession = Depends(get_db),
):
    """Borra en lotes los registros más viejos que `months` meses (evita
    lockear la tabla entera en MySQL 5.7 con un DELETE masivo)."""
    corte = datetime.now(UTC) - timedelta(days=months * 30)
    deleted_total = 0

    while True:
        ids = (await db.execute(
            select(AuditLog.id).where(AuditLog.timestamp < corte).limit(_PURGE_BATCH_SIZE)
        )).scalars().all()
        if not ids:
            break
        await db.execute(delete(AuditLog).where(AuditLog.id.in_(ids)))
        await db.commit()
        deleted_total += len(ids)
        if len(ids) < _PURGE_BATCH_SIZE:
            break

    return PurgeResult(ok=True, deleted=deleted_total, months=months)
