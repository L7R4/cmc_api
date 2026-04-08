from datetime import datetime, date, timedelta, UTC
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.params import Query
from sqlalchemy import insert, select, func, or_, cast, String, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_scope
from app.db.database import get_db
from app.db.models import ListadoMedico, Role, SolicitudRegistro, UserRole
from app.modules.solicitudes.schemas import ApproveIn, RejectIn, SolicitudDetailOut, SolicitudListItem
from app.services.email import send_email_resend
from app.services.mail_templates import build_approval_email, build_rejection_email

router = APIRouter()


def _as_utc_aware(x: datetime | date | None) -> datetime | None:
    if x is None:
        return None
    if isinstance(x, datetime):
        return x.replace(tzinfo=UTC) if x.tzinfo is None else x.astimezone(UTC)
    if isinstance(x, date):
        return datetime(x.year, x.month, x.day, tzinfo=UTC)
    raise TypeError(f"Tipo no soportado: {type(x)!r}")


@router.get("/", response_model=List[SolicitudListItem])
async def listar_solicitudes(
    estado: Optional[str] = None,
    q: Optional[str] = None,
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
    nuevos_dias: int = 7,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    S, M = SolicitudRegistro, ListadoMedico

    stmt = select(
        S.id.label("id"),
        S.medico_id.label("medico_id"),
        S.estado.label("estado"),
        S.created_at.label("created_at"),
        S.observaciones.label("observaciones"),
        M.NOMBRE.label("nombre"),
        M.MAIL_PARTICULAR.label("email"),
        M.TELE_PARTICULAR.label("telefono"),
        M.CATEGORIA.label("categoria"),
        M.FECHA_INGRESO.label("fecha_ingreso"),
        M.DOCUMENTO.label("documento"),
    ).join(M, M.ID == S.medico_id)

    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                M.NOMBRE.ilike(like),
                M.MAIL_PARTICULAR.ilike(like),
                cast(M.DOCUMENTO, String).ilike(like),
            )
        )

    if desde:
        stmt = stmt.where(S.created_at >= desde)
    if hasta:
        stmt = stmt.where(S.created_at <= hasta)

    now = datetime.now(UTC)
    if estado and estado != "todas":
        if estado == "nueva":
            limite = now - timedelta(days=nuevos_dias)
            stmt = stmt.where(
                and_(
                    S.estado == "pendiente",
                    S.created_at >= limite
                )
            )
        else:
            stmt = stmt.where(S.estado == estado)

    stmt = stmt.order_by(S.created_at.desc()).offset(skip).limit(limit)

    rows = (await db.execute(stmt)).mappings().all()

    limite_nueva = now - timedelta(days=nuevos_dias)
    out: List[SolicitudListItem] = []
    for r in rows:
        base_estado = str(r["estado"]).lower().strip()
        created_at_aware = _as_utc_aware(r["created_at"])
        es_nueva = base_estado == "pendiente" and (
            created_at_aware is not None and created_at_aware >= (now - timedelta(days=nuevos_dias))
        )
        ui_status = "nueva" if es_nueva else base_estado

        out.append(SolicitudListItem(
            id=int(r["id"]),
            medico_id=int(r["medico_id"]),
            name=str(r["nombre"] or "-"),
            email=r["email"],
            phone=r["telefono"],
            status=ui_status,
            submitted_date=created_at_aware,
            member_type=r["categoria"] if r["categoria"] else None,
            join_date=r["fecha_ingreso"],
            observations=r["observaciones"],
        ))
    return out


@router.get("/stats/counts")
async def solicitudes_stats_counts(
    q: Optional[str] = None,
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
    nuevos_dias: int = 7,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, int]:
    S, M = SolicitudRegistro, ListadoMedico
    now = datetime.now(UTC)
    limite = now - timedelta(days=nuevos_dias)

    def base():
        stmt = select(func.count()).select_from(S).join(M, M.ID == S.medico_id)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                or_(
                    M.NOMBRE.ilike(like),
                    M.MAIL_PARTICULAR.ilike(like),
                    cast(M.DOCUMENTO, String).ilike(like),
                )
            )
        if desde:
            stmt = stmt.where(S.created_at >= desde)
        if hasta:
            stmt = stmt.where(S.created_at <= hasta)
        return stmt

    total = (await db.execute(base())).scalar_one() or 0
    pend_total = (await db.execute(base().where(S.estado == "pendiente"))).scalar_one() or 0
    nueva = (await db.execute(base().where(and_(S.estado == "pendiente", S.created_at >= limite)))).scalar_one() or 0
    pendiente = max(pend_total - nueva, 0)
    aprobada = (await db.execute(base().where(S.estado == "aprobada"))).scalar_one() or 0
    rechazada = (await db.execute(base().where(S.estado == "rechazada"))).scalar_one() or 0

    return {
        "total": int(total),
        "nueva": int(nueva),
        "pendiente": int(pendiente),
        "aprobada": int(aprobada),
        "rechazada": int(rechazada),
    }


@router.get("/stats/monthly")
async def solicitudes_stats_monthly(
    metric: Optional[str] = Query("submitted", pattern="^(submitted|approved)$"),
    q: Optional[str] = None,
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
):
    S, M = SolicitudRegistro, ListadoMedico
    field = S.created_at if metric == "submitted" else S.aprobado_at

    stmt = select(
        func.extract("year", field).label("year"),
        func.extract("month", field).label("month"),
        func.count().label("count"),
    ).select_from(S).join(M, M.ID == S.medico_id).where(field.is_not(None))

    if metric == "approved":
        stmt = stmt.where(S.estado == "aprobada")

    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                M.NOMBRE.ilike(like),
                M.MAIL_PARTICULAR.ilike(like),
                cast(M.DOCUMENTO, String).ilike(like),
            )
        )
    if desde:
        stmt = stmt.where(field >= desde)
    if hasta:
        stmt = stmt.where(field <= hasta)

    stmt = stmt.group_by("year", "month").order_by("year", "month")
    rows = (await db.execute(stmt)).all()
    return [{"year": int(r.year), "month": int(r.month), "count": int(r.count)} for r in rows]


@router.get("/{sid}", response_model=SolicitudDetailOut)
async def obtener_solicitud(sid: int, db: AsyncSession = Depends(get_db)):
    S, M = SolicitudRegistro, ListadoMedico

    stmt = (
        select(
            S.id.label("id"),
            S.medico_id.label("medico_id"),
            S.estado.label("estado"),
            S.created_at.label("created_at"),
            S.observaciones.label("observaciones"),
            M.NOMBRE.label("nombre"),
            M.MAIL_PARTICULAR.label("email"),
            M.TELE_PARTICULAR.label("telefono"),
            M.CATEGORIA.label("categoria"),
            M.FECHA_INGRESO.label("fecha_ingreso"),
            M.DOCUMENTO.label("documento"),
            M.PROVINCIA.label("provincia"),
            M.localidad.label("localidad"),
        )
        .join(M, M.ID == S.medico_id)
        .where(S.id == sid)
    )

    row = (await db.execute(stmt)).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")

    base_estado = str(row["estado"]).lower().strip()
    created_at_aware = _as_utc_aware(row["created_at"])

    return SolicitudDetailOut(
        id=int(row["id"]),
        medico_id=int(row["medico_id"]),
        name=str(row["nombre"] or "-"),
        email=row["email"],
        phone=row["telefono"],
        status=base_estado,
        submitted_date=created_at_aware,
        member_type=row["categoria"],
        join_date=row["fecha_ingreso"],
        observations=row["observaciones"],
        rejection_reason=None,
        documento=row["documento"],
        provincia=row["provincia"],
        localidad=row["localidad"],
        categoria=row["categoria"],
    )


@router.post("/{sid}/approve")
async def approve_solicitud(
    sid: int,
    body: ApproveIn,
    db: AsyncSession = Depends(get_db),
):
    sol = await db.get(SolicitudRegistro, sid)
    if not sol:
        raise HTTPException(404, "Solicitud no encontrada")
    if sol.estado != "pendiente":
        raise HTTPException(400, f"No aprobable: estado={sol.estado}")

    med = await db.get(ListadoMedico, sol.medico_id)
    if not med:
        raise HTTPException(404, "Médico no encontrado")

    nro_socio = body.nro_socio
    if not nro_socio:
        q = await db.execute(select(func.max(ListadoMedico.NRO_SOCIO)))
        nro_socio = (q.scalar() or 0) + 1

    med.NRO_SOCIO = nro_socio
    med.EXISTE = "S"
    med.FECHA_INGRESO = datetime.now(UTC).date()

    sol.estado = "aprobada"
    sol.aprobado_por = None
    sol.aprobado_at = datetime.now(UTC)
    sol.observaciones = body.observaciones

    rol_medico = await db.get(Role, 2)
    if not rol_medico:
        rol_medico = (await db.execute(
            select(Role).where(Role.name == "Medico")
        )).scalar_one_or_none()

    if not rol_medico:
        raise HTTPException(500, "No existe el rol 'Medico' (id=2). Cargá el rol en la tabla 'roles'.")

    already_has_role = (await db.execute(
        select(UserRole).where(
            UserRole.user_id == med.ID,
            UserRole.role_id == rol_medico.id
        )
    )).first()

    if not already_has_role:
        await db.execute(insert(UserRole).values(user_id=med.ID, role_id=rol_medico.id))

    await db.commit()

    subject = "✅ Solicitud Aprobada — Próximos pasos"
    html, text = build_approval_email(
        name=med.NOMBRE,
        member_type=med.CATEGORIA,
        join_date=med.FECHA_INGRESO,
        observations=body.observaciones,
    )
    if med.MAIL_PARTICULAR:
        send_email_resend(med.MAIL_PARTICULAR, subject, html)

    return {"ok": True, "nro_socio": med.NRO_SOCIO}


@router.post("/{sid}/reject")
async def reject_solicitud(
    sid: int,
    body: RejectIn,
    db: AsyncSession = Depends(get_db),
):
    sol = await db.get(SolicitudRegistro, sid)
    if not sol:
        raise HTTPException(404, "Solicitud no encontrada")
    if sol.estado != "pendiente":
        raise HTTPException(400, f"No rechazable: estado={sol.estado}")

    med = await db.get(ListadoMedico, sol.medico_id)
    if not med:
        raise HTTPException(404, "Médico no encontrado")

    sol.estado = "rechazada"
    sol.observaciones = body.observaciones
    await db.commit()

    subject = "❌ Solicitud Rechazada — Información"
    html, text = build_rejection_email(
        name=med.NOMBRE,
        reason=body.observaciones or "Sin detalle.",
    )

    if med.MAIL_PARTICULAR:
        send_email_resend(med.MAIL_PARTICULAR, subject, html)

    return {"ok": True}
