"""
Compensaciones: plata a favor (+) o en contra (−) de un socio, sin un concepto del catálogo
(un regalo de cumpleaños, un reintegro, una rotura). Puede ir en cuotas: cada cuota es una fila,
las de un plan comparten `plan_id` (el id de la cuota 1) y cada una lleva su mes.

  pendiente → en_pago   sola cuando llega su mes (compensacion_update.enrolar_cuotas_del_mes),
                        o a mano: pasar al pago abierto (PATCH /{id}/estado)
  en_pago   → pendiente quitar del pago abierto: la cuota pasa al mes siguiente al del pago
                        (y corre las que le siguen), si no volvería a entrar sola
  pendiente ⇄ en_caja   pagada en caja, sin pasar por un pago
  pendiente → anulada   POST /{id}/anular (todo lo pendiente del plan, o sólo la cuota;
                        queda en la lista, con su motivo)
  en_pago   → pagada    lo hace el cierre del pago        (pagos/routes.py)
  pagada    → en_pago   lo hace la reapertura del pago
  en_pago/pagada → pendiente   lo hace la eliminación del pago

Las anteriores a las cuotas no tienen mes: entran a mano, como antes.

Dentro del pago funciona como un débito o crédito suelto, sin obra social: suma al reconocido
y al disponible del socio, pero NO es producción (no entra en las bases de los porcentuales).
"""
import datetime
from decimal import ROUND_DOWN, Decimal
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
from app.services.compensacion_update import mes_siguiente, posponer_cuotas

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
        plan_id=c.plan_id, cuota_nro=c.cuota_nro, cuotas_total=c.cuotas_total,
        importe=c.importe, importe_total=c.importe_total,
        leyenda=c.leyenda, nota_interna=c.nota_interna,
        estado=c.estado, mes_aplicar=c.mes_aplicar, anio_aplicar=c.anio_aplicar,
        pago_id=c.pago_id, fecha_caja=c.fecha_caja,
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


async def _cuotas_del_plan(db: AsyncSession, comp: Compensacion) -> list[Compensacion]:
    return list((await db.execute(
        select(Compensacion)
        .where(Compensacion.plan_id == (comp.plan_id or comp.id))
        .order_by(Compensacion.cuota_nro)
    )).scalars().all())


def _repartir(total: Decimal, n: int) -> list[Decimal]:
    """Partes iguales (truncadas al centavo) y el resto en la última; con el signo del total."""
    signo = Decimal(-1) if total < 0 else Decimal(1)
    base = (abs(total) / n).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    if base == 0:
        raise HTTPException(422, "El importe no alcanza para tantas cuotas")
    partes = [base] * n
    partes[-1] = abs(total) - base * (n - 1)
    return [p * signo for p in partes]


def _mes_inicio(mes: Optional[int], anio: Optional[int], pago: Optional[Pago]) -> tuple[int, int]:
    if (mes is None) != (anio is None):
        raise HTTPException(422, "Indicá el mes y el año de la primera cuota, o ninguno de los dos")
    if mes is not None:
        return mes, anio
    if pago is not None:
        return pago.mes, pago.anio
    hoy = datetime.date.today()
    return hoy.month, hoy.year


def _armar_cuotas(
    filas: list[Compensacion], importes: list[Decimal], total: Decimal, mes: Optional[int], anio: Optional[int]
) -> None:
    """Numera las cuotas, les pone su importe y su mes (una por mes). Sin mes: entran a mano."""
    n = len(importes)
    for i, (c, imp) in enumerate(zip(filas, importes), start=1):
        c.cuota_nro, c.cuotas_total = i, n
        c.importe, c.importe_total = imp, total
        c.mes_aplicar, c.anio_aplicar = mes, anio
        if mes is not None:
            mes, anio = mes_siguiente(mes, anio)


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
    plan_id: Optional[int] = Query(None, description="Las cuotas de un plan"),
    db: AsyncSession = Depends(get_db),
):
    # Los planes más nuevos primero y, dentro de cada plan, las cuotas en orden (por plan y no por
    # fecha de alta: una cuota que se suma al editar tiene otra fecha).
    stmt = _select_leer().order_by(Compensacion.plan_id.desc(), Compensacion.cuota_nro)
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
    if plan_id is not None:
        stmt = stmt.where(Compensacion.plan_id == plan_id)

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
# POST / — crear (una cuota o un plan de cuotas)
# ================================================
@router.post("/", response_model=CompensacionRead, status_code=201)
async def crear_compensacion(
    payload: CompensacionCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Devuelve la cuota 1 (con plan_id y cuotas_total para leer el resto)."""
    if not await db.get(ListadoMedico, payload.medico_id):
        raise HTTPException(404, "El socio no existe")
    if payload.destino == "caja" and payload.cuotas > 1:
        raise HTTPException(422, "Pagada en caja va en una sola cuota")

    pago = await _pago_abierto(db)
    if payload.destino == "pago":
        if not pago:
            raise HTTPException(409, "No hay un pago abierto al que pasar la compensación")
        mes, anio = pago.mes, pago.anio
    else:
        mes, anio = _mes_inicio(payload.mes_inicio, payload.anio_inicio, pago)

    importes = _repartir(payload.importe, payload.cuotas)
    filas = [
        Compensacion(
            medico_id=payload.medico_id,
            leyenda=payload.leyenda,
            nota_interna=(payload.nota_interna or "").strip() or None,
            estado="pendiente",
            created_by_user=user.get("uid"),
        )
        for _ in importes
    ]
    _armar_cuotas(filas, importes, payload.importe, mes, anio)
    if payload.destino == "caja":
        filas[0].estado = "en_caja"
        filas[0].fecha_caja = datetime.date.today()

    db.add_all(filas)
    await db.flush()
    for c in filas:
        c.plan_id = filas[0].id
    await db.flush()

    if pago and payload.destino != "caja":
        # Las cuotas cuyo mes llegó entran al pago; el socio pasa a participar: entran sus cuotas
        # de deducciones pendientes y se recalcula la vista previa.
        await generar_y_recalcular_porcentuales(db, pago.id)
    await db.commit()
    return await _leer(db, filas[0].id)


# ================================================
# GET /{id}
# ================================================
@router.get("/{compensacion_id}", response_model=CompensacionRead)
async def obtener_compensacion(compensacion_id: int, db: AsyncSession = Depends(get_db)):
    return await _leer(db, compensacion_id)


# ================================================
# PATCH /{id} — editar el plan de una cuota pendiente
# ================================================
@router.patch("/{compensacion_id}", response_model=CompensacionRead)
async def editar_compensacion(
    compensacion_id: int, payload: CompensacionUpdate, db: AsyncSession = Depends(get_db)
):
    comp = await _get(db, compensacion_id)
    if comp.estado != "pendiente":
        raise HTTPException(409, "Solo se edita una compensación pendiente. Quitala del pago o reabrila primero.")
    plan = await _cuotas_del_plan(db, comp)

    campos = payload.model_fields_set
    de_plata = {"medico_id", "importe", "cuotas", "mes_inicio", "anio_inicio"} & {
        k for k in campos if getattr(payload, k) is not None
    }
    pago = await _pago_abierto(db)
    if de_plata:
        if any(c.estado != "pendiente" for c in plan):
            raise HTTPException(
                409,
                "Alguna cuota ya entró a un pago, se pagó o se anuló: sólo se cambian la leyenda y la nota. "
                "Para lo demás, anulá las cuotas pendientes y cargá otra compensación.",
            )
        medico_id = payload.medico_id if payload.medico_id is not None else comp.medico_id
        if medico_id != comp.medico_id and not await db.get(ListadoMedico, medico_id):
            raise HTTPException(404, "El socio no existe")
        total = payload.importe if payload.importe is not None else (
            plan[0].importe_total if plan[0].importe_total is not None else sum((c.importe for c in plan), Decimal("0"))
        )
        n = payload.cuotas if payload.cuotas is not None else len(plan)
        if payload.mes_inicio is not None or payload.anio_inicio is not None:
            mes, anio = _mes_inicio(payload.mes_inicio, payload.anio_inicio, pago)
        elif plan[0].mes_aplicar is not None:
            mes, anio = plan[0].mes_aplicar, plan[0].anio_aplicar
        else:
            mes = anio = None  # anterior a las cuotas: sigue entrando a mano si queda en una
            if n > 1:
                mes, anio = _mes_inicio(None, None, pago)

        importes = _repartir(total, n)
        filas = plan[:n]
        for c in plan[n:]:
            await db.delete(c)
        while len(filas) < n:
            nueva = Compensacion(
                plan_id=plan[0].plan_id or plan[0].id, leyenda=plan[0].leyenda,
                nota_interna=plan[0].nota_interna, estado="pendiente",
                created_by_user=plan[0].created_by_user,
            )
            db.add(nueva)
            filas.append(nueva)
        for c in filas:
            c.medico_id = medico_id
        _armar_cuotas(filas, importes, total, mes, anio)
        plan = filas

    if payload.leyenda is not None:
        leyenda = payload.leyenda.strip()
        if not leyenda:
            raise HTTPException(422, "La leyenda del recibo es obligatoria")
        for c in plan:
            if c.estado in ("pendiente", "en_pago"):
                c.leyenda = leyenda
    if "nota_interna" in campos:
        nota = (payload.nota_interna or "").strip() or None
        for c in plan:
            if c.estado in ("pendiente", "en_pago"):
                c.nota_interna = nota

    await db.flush()
    if de_plata and pago:
        await generar_y_recalcular_porcentuales(db, pago.id)
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
        if pago and comp.mes_aplicar is not None:
            await posponer_cuotas(db, comp, pago)

    elif transicion == ("pendiente", "en_caja"):
        comp.estado = "en_caja"
        comp.fecha_caja = datetime.date.today()

    elif transicion == ("en_caja", "pendiente"):
        comp.estado = "pendiente"
        comp.fecha_caja = None
        # Si su mes ya llegó, vuelve a entrar sola al pago abierto.
        if comp.mes_aplicar is not None:
            pago = await _pago_abierto(db)
            pago_afectado = pago.id if pago else None

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
    """Solo una pendiente; con alcance «plan», también las demás cuotas pendientes del plan (las
    que entraron a un pago o se pagaron no se tocan). No se borra: queda en la lista con su motivo."""
    comp = await _get(db, compensacion_id)
    if comp.estado != "pendiente":
        raise HTTPException(409, "Solo se anula una compensación pendiente")
    motivo = payload.motivo.strip()
    if not motivo:
        raise HTTPException(422, "El motivo es obligatorio")
    cuotas = [comp]
    if payload.alcance == "plan":
        cuotas = [c for c in await _cuotas_del_plan(db, comp) if c.estado == "pendiente"]
    for c in cuotas:
        c.estado = "anulada"
        c.motivo_anulacion = motivo
    await db.commit()
    return await _leer(db, comp.id)
