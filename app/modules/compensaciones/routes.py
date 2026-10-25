"""
Compensaciones: plata a favor (+) o en contra (−) de un socio, sin un concepto del catálogo
(un regalo de cumpleaños, un reintegro, una rotura). Un solo importe, sin cuotas.

  pendiente → en_pago   pasar al pago abierto              (PATCH /{id}/estado)
  en_pago   → pendiente quitar del pago abierto
  pendiente ⇄ en_caja   pagada en caja, sin pasar por un pago
  pendiente → anulada   POST /{id}/anular (queda en la lista, con su motivo)
  en_pago   → pagada    lo hace el cierre del pago        (pagos/routes.py)
  pagada    → en_pago   lo hace la reapertura del pago
  en_pago/pagada → pendiente   lo hace la eliminación del pago

Dentro del pago funciona como un débito o crédito suelto, sin obra social: suma al reconocido
y al disponible del socio, pero NO es producción (no entra en las bases de los porcentuales).
"""
import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.auth.deps import get_current_user
from app.db.database import get_db
from app.db.models import Compensacion, ListadoMedico, Pago
from app.modules.compensaciones.schemas import (
    CompensacionAnularPayload,
    CompensacionCreate,
    CompensacionEstadoPayload,
    CompensacionLista,
    CompensacionRead,
    CompensacionTotales,
    CompensacionUpdate,
)
from app.modules.deducciones.service import generar_y_recalcular_porcentuales

router = APIRouter()

Creador = aliased(ListadoMedico)


def _select_leer():
    return (
        select(
            Compensacion,
            ListadoMedico.NOMBRE.label("medico_nombre"),
            ListadoMedico.NRO_SOCIO.label("medico_nro_socio"),
            Creador.NOMBRE.label("creada_por"),
        )
        .join(ListadoMedico, ListadoMedico.ID == Compensacion.medico_id)
        .outerjoin(Creador, Creador.ID == Compensacion.created_by_user)
    )


def _a_read(r) -> CompensacionRead:
    c: Compensacion = r[0]
    return CompensacionRead(
        id=c.id, medico_id=c.medico_id,
        medico_nombre=(r.medico_nombre or "").strip() or None,
        medico_nro_socio=r.medico_nro_socio,
        importe=c.importe, leyenda=c.leyenda, nota_interna=c.nota_interna,
        estado=c.estado, pago_id=c.pago_id, fecha_caja=c.fecha_caja,
        motivo_anulacion=c.motivo_anulacion, created_at=c.created_at,
        creada_por=(r.creada_por or "").strip() or None,
    )


async def _leer(db: AsyncSession, comp_id: int) -> CompensacionRead:
    row = (await db.execute(_select_leer().where(Compensacion.id == comp_id))).first()
    if not row:
        raise HTTPException(404, "Compensación no encontrada")
    return _a_read(row)


async def _get(db: AsyncSession, comp_id: int) -> Compensacion:
    c = await db.get(Compensacion, comp_id)
    if not c:
        raise HTTPException(404, "Compensación no encontrada")
    return c


async def _pago_abierto(db: AsyncSession) -> Optional[Pago]:
    return (await db.execute(select(Pago).where(Pago.estado == "A").limit(1))).scalars().first()


# ================================================
# GET /lista
# ================================================
@router.get("/lista", response_model=CompensacionLista)
async def listar_compensaciones(
    medico_id: Optional[int] = Query(None),
    estado: Optional[str] = Query(
        None, description="pendiente | en_pago | pagada | en_caja | anulada | todas. Sin valor: todas menos las anuladas."
    ),
    sentido: Optional[str] = Query(None, description="favor | contra"),
    desde: Optional[datetime.date] = Query(None),
    hasta: Optional[datetime.date] = Query(None),
    pago_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    stmt = _select_leer().order_by(Compensacion.created_at.desc(), Compensacion.id.desc())
    if medico_id is not None:
        stmt = stmt.where(Compensacion.medico_id == medico_id)
    if estado == "todas":
        pass
    elif estado:
        stmt = stmt.where(Compensacion.estado == estado)
    else:
        stmt = stmt.where(Compensacion.estado != "anulada")
    if sentido == "favor":
        stmt = stmt.where(Compensacion.importe > 0)
    elif sentido == "contra":
        stmt = stmt.where(Compensacion.importe < 0)
    if desde is not None:
        stmt = stmt.where(func.date(Compensacion.created_at) >= desde)
    if hasta is not None:
        stmt = stmt.where(func.date(Compensacion.created_at) <= hasta)
    if pago_id is not None:
        stmt = stmt.where(Compensacion.pago_id == pago_id)

    rows = (await db.execute(stmt)).all()
    items = [_a_read(r) for r in rows]
    vivas = [i for i in items if i.estado != "anulada"]
    return CompensacionLista(
        items=items,
        totales=CompensacionTotales(
            a_favor=sum((i.importe for i in vivas if i.importe > 0), Decimal("0")),
            en_contra=sum((-i.importe for i in vivas if i.importe < 0), Decimal("0")),
            pendientes=sum(1 for i in vivas if i.estado == "pendiente"),
        ),
    )


# ================================================
# POST / — crear
# ================================================
@router.post("/", response_model=CompensacionRead, status_code=201)
async def crear_compensacion(
    payload: CompensacionCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    if not await db.get(ListadoMedico, payload.medico_id):
        raise HTTPException(404, "El socio no existe")

    comp = Compensacion(
        medico_id=payload.medico_id,
        importe=payload.importe,
        leyenda=payload.leyenda,
        nota_interna=(payload.nota_interna or "").strip() or None,
        estado="pendiente",
        created_by_user=user.get("uid"),
    )
    pago_afectado: Optional[int] = None
    if payload.destino == "pago":
        pago = await _pago_abierto(db)
        if not pago:
            raise HTTPException(409, "No hay un pago abierto al que pasar la compensación")
        comp.estado = "en_pago"
        comp.pago_id = pago.id
        pago_afectado = pago.id
    elif payload.destino == "caja":
        comp.estado = "en_caja"
        comp.fecha_caja = datetime.date.today()

    db.add(comp)
    await db.flush()
    if pago_afectado:
        # El socio pasa a participar del pago: entran sus cuotas pendientes y se recalcula la vista previa.
        await generar_y_recalcular_porcentuales(db, pago_afectado)
    await db.commit()
    return await _leer(db, comp.id)


# ================================================
# GET /{id}
# ================================================
@router.get("/{compensacion_id}", response_model=CompensacionRead)
async def obtener_compensacion(compensacion_id: int, db: AsyncSession = Depends(get_db)):
    return await _leer(db, compensacion_id)


# ================================================
# PATCH /{id} — editar una pendiente
# ================================================
@router.patch("/{compensacion_id}", response_model=CompensacionRead)
async def editar_compensacion(
    compensacion_id: int, payload: CompensacionUpdate, db: AsyncSession = Depends(get_db)
):
    comp = await _get(db, compensacion_id)
    if comp.estado != "pendiente":
        raise HTTPException(409, "Solo se edita una compensación pendiente. Quitala del pago o reabrila primero.")
    if payload.medico_id is not None and payload.medico_id != comp.medico_id:
        if not await db.get(ListadoMedico, payload.medico_id):
            raise HTTPException(404, "El socio no existe")
        comp.medico_id = payload.medico_id
    if payload.importe is not None:
        comp.importe = payload.importe
    if payload.leyenda is not None:
        leyenda = payload.leyenda.strip()
        if not leyenda:
            raise HTTPException(422, "La leyenda del recibo es obligatoria")
        comp.leyenda = leyenda
    if "nota_interna" in payload.model_fields_set:
        comp.nota_interna = (payload.nota_interna or "").strip() or None
    await db.commit()
    return await _leer(db, comp.id)


# ================================================
# PATCH /{id}/estado — pasar al pago, quitar del pago, pagar en caja
# ================================================
@router.patch("/{compensacion_id}/estado", response_model=CompensacionRead)
async def cambiar_estado(
    compensacion_id: int, payload: CompensacionEstadoPayload, db: AsyncSession = Depends(get_db)
):
    comp = await _get(db, compensacion_id)
    nuevo = payload.estado
    if comp.estado == nuevo:
        raise HTTPException(409, f"La compensación ya está en estado '{nuevo}'")

    transicion = (comp.estado, nuevo)
    pago_afectado: Optional[int] = None

    if transicion == ("pendiente", "en_pago"):
        pago = await _pago_abierto(db)
        if not pago:
            raise HTTPException(409, "No hay un pago abierto al que pasar la compensación")
        comp.estado = "en_pago"
        comp.pago_id = pago.id
        pago_afectado = pago.id

    elif transicion == ("en_pago", "pendiente"):
        pago = await db.get(Pago, comp.pago_id) if comp.pago_id else None
        if pago and pago.estado != "A":
            raise HTTPException(409, "No se puede quitar una compensación de un pago cerrado")
        pago_afectado = comp.pago_id
        comp.estado = "pendiente"
        comp.pago_id = None

    elif transicion == ("pendiente", "en_caja"):
        comp.estado = "en_caja"
        comp.fecha_caja = datetime.date.today()

    elif transicion == ("en_caja", "pendiente"):
        comp.estado = "pendiente"
        comp.fecha_caja = None

    else:
        if comp.estado == "pagada":
            raise HTTPException(
                409, "La compensación ya se pagó con un pago cerrado. Un error se corrige con otra compensación de signo contrario."
            )
        if comp.estado == "anulada":
            raise HTTPException(409, "La compensación está anulada")
        raise HTTPException(409, f"Transición '{comp.estado}' → '{nuevo}' no permitida")

    if pago_afectado:
        # Cambia quién participa del pago y cuánto tiene disponible cada socio.
        await generar_y_recalcular_porcentuales(db, pago_afectado)
    await db.commit()
    return await _leer(db, comp.id)


# ================================================
# POST /{id}/anular
# ================================================
@router.post("/{compensacion_id}/anular", response_model=CompensacionRead)
async def anular_compensacion(
    compensacion_id: int, payload: CompensacionAnularPayload, db: AsyncSession = Depends(get_db)
):
    """Solo una pendiente. No se borra: queda en la lista con su motivo."""
    comp = await _get(db, compensacion_id)
    if comp.estado != "pendiente":
        raise HTTPException(409, "Solo se anula una compensación pendiente")
    motivo = payload.motivo.strip()
    if not motivo:
        raise HTTPException(422, "El motivo es obligatorio")
    comp.estado = "anulada"
    comp.motivo_anulacion = motivo
    await db.commit()
    return await _leer(db, comp.id)
