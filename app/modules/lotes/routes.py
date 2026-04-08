from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.database import get_db
from app.db.models import (
    Ajuste,
    GuardarAtencion,
    Liquidacion,
    LoteAjuste,
    Pago,
)
from app.db.models.catalogs import ObrasSociales, Periodos
from app.db.models.medico import ListadoMedico
from app.modules.liquidacion.service import recalcular_totales_de_liquidacion
from app.modules.lotes.schemas import (
    AjusteCreate,
    AjusteRead,
    AjusteUpdate,
    AtencionSearchRow,
    LoteAjusteCreate,
    LoteAjusteRead,
    LoteCambiarEstadoPayload,
    LoteListaRow,
    LoteRefacturacionCreate,
    LoteSinFacturaCreate,
)

router = APIRouter()


# ================================================
# Helper: recalcular totales del lote
# ================================================
async def recalcular_totales_lote(db: AsyncSession, lote_id: int) -> None:
    res = await db.execute(
        select(
            func.coalesce(func.sum(case((Ajuste.tipo == "d", Ajuste.honorarios + Ajuste.gastos), else_=0)), 0).label("debitos"),
            func.coalesce(func.sum(case((Ajuste.tipo == "c", Ajuste.honorarios + Ajuste.gastos), else_=0)), 0).label("creditos"),
        ).where(Ajuste.lote_id == lote_id)
    )
    row = res.first()
    lote = await db.get(LoteAjuste, lote_id)
    if lote:
        lote.total_debitos = Decimal(str(row.debitos or 0))
        lote.total_creditos = Decimal(str(row.creditos or 0))
    await db.flush()


def _build_ajuste_read(ajuste: Ajuste, atencion=None, medico=None) -> AjusteRead:
    return AjusteRead(
        id=ajuste.id,
        lote_id=ajuste.lote_id,
        tipo=ajuste.tipo,
        medico_id=ajuste.medico_id,
        obra_social_id=ajuste.obra_social_id,
        honorarios=ajuste.honorarios,
        gastos=ajuste.gastos,
        total=ajuste.honorarios + ajuste.gastos,
        observacion=ajuste.observacion,
        id_atencion=ajuste.id_atencion,
        origen=ajuste.origen,
        nombre_afiliado=atencion.NOMBRE_AFILIADO if atencion else None,
        # Para sin_factura (id_atencion=NULL) se usa ListadoMedico como fallback
        nombre_prestador=atencion.NOMBRE_PRESTADOR if atencion else (medico.NOMBRE if medico else None),
        nro_socio=atencion.NRO_SOCIO if atencion else (medico.NRO_SOCIO if medico else None),
        nro_consulta=atencion.NRO_CONSULTA if atencion else None,
        valor_cirujia=atencion.VALOR_CIRUJIA if atencion else None,
        codigo_prestacion=atencion.CODIGO_PRESTACION if atencion else None,
        fecha_prestacion=str(atencion.FECHA_PRESTACION) if atencion and atencion.FECHA_PRESTACION else None,
    )


def _lote_with_ajustes(lote: LoteAjuste, ajuste_rows=None) -> LoteAjusteRead:
    if ajuste_rows is not None:
        ajustes = [_build_ajuste_read(a, ga, med) for a, ga, med in ajuste_rows]
    else:
        ajustes = [AjusteRead.model_validate(a) for a in (lote.ajustes or [])]
    return LoteAjusteRead(
        id=lote.id,
        obra_social_id=lote.obra_social_id,
        mes_periodo=lote.mes_periodo,
        anio_periodo=lote.anio_periodo,
        tipo=lote.tipo,
        snap_origen_id=lote.snap_origen_id,
        estado=lote.estado,
        pago_id=lote.pago_id,
        total_debitos=lote.total_debitos,
        total_creditos=lote.total_creditos,
        ajustes=ajustes,
    )


async def _get_enriched_ajuste_rows(db: AsyncSession, lote_id: int):
    """Devuelve lista de (Ajuste, GuardarAtencion | None, ListadoMedico | None) para un lote."""
    stmt = (
        select(Ajuste, GuardarAtencion, ListadoMedico)
        .outerjoin(GuardarAtencion, Ajuste.id_atencion == GuardarAtencion.ID)
        .outerjoin(ListadoMedico, Ajuste.medico_id == ListadoMedico.ID)
        .where(Ajuste.lote_id == lote_id)
    )
    return (await db.execute(stmt)).all()


async def _get_lote_with_ajustes(db: AsyncSession, lote_id: int) -> LoteAjuste:
    stmt = (
        select(LoteAjuste)
        .options(selectinload(LoteAjuste.ajustes))
        .where(LoteAjuste.id == lote_id)
    )
    lote = (await db.execute(stmt)).scalars().first()
    if not lote:
        raise HTTPException(404, "Lote no encontrado")
    return lote


# ================================================
# POST /snaps/obtener_o_crear
# ================================================
@router.post("/snaps/obtener_o_crear", response_model=LoteAjusteRead)
async def obtener_o_crear_lote(
    payload: LoteAjusteCreate,
    db: AsyncSession = Depends(get_db),
):
    """Devuelve el lote tipo='normal' existente para ese OS+período (en cualquier estado). Si no existe, crea uno en estado='A'. Solo puede haber un lote normal por OS+período."""
    stmt = (
        select(LoteAjuste)
        .options(selectinload(LoteAjuste.ajustes))
        .where(
            LoteAjuste.obra_social_id == payload.obra_social_id,
            LoteAjuste.mes_periodo == payload.mes_periodo,
            LoteAjuste.anio_periodo == payload.anio_periodo,
            LoteAjuste.tipo == "normal",
        )
        .limit(1)
    )
    lote = (await db.execute(stmt)).scalars().first()

    if not lote:
        lote = LoteAjuste(
            obra_social_id=payload.obra_social_id,
            mes_periodo=payload.mes_periodo,
            anio_periodo=payload.anio_periodo,
            tipo="normal",
            estado="A",
            total_debitos=Decimal("0"),
            total_creditos=Decimal("0"),
        )
        db.add(lote)
        await db.commit()
        await db.refresh(lote)

    rows = await _get_enriched_ajuste_rows(db, lote.id)
    return _lote_with_ajustes(lote, rows)


# ================================================
# POST /snaps/crear_refacturacion
# ================================================
@router.post("/snaps/crear_refacturacion", response_model=LoteAjusteRead, status_code=201)
async def crear_lote_refacturacion(
    payload: LoteRefacturacionCreate,
    db: AsyncSession = Depends(get_db),
):
    """Crea un lote tipo='refacturacion'. Sin restricción de cantidad por OS+período."""
    lote = LoteAjuste(
        obra_social_id=payload.obra_social_id,
        mes_periodo=payload.mes_periodo,
        anio_periodo=payload.anio_periodo,
        tipo="refacturacion",
        snap_origen_id=payload.snap_origen_id,
        estado="A",
        total_debitos=Decimal("0"),
        total_creditos=Decimal("0"),
    )
    db.add(lote)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Ya existe un lote de refacturación para ese lote origen (uq_lote_origen)")

    await db.commit()
    await db.refresh(lote)
    return _lote_with_ajustes(lote)


# ================================================
# POST /snaps/crear_sin_factura
# ================================================
@router.post("/snaps/crear_sin_factura", response_model=LoteAjusteRead, status_code=201)
async def crear_lote_sin_factura(
    payload: LoteSinFacturaCreate,
    db: AsyncSession = Depends(get_db),
):
    """Crea un lote tipo='sin_factura'. No requiere período cerrado en Periodos.
    Sin restricción de cantidad por OS+período. Los ajustes deben incluir medico_id explícito."""
    lote = LoteAjuste(
        obra_social_id=payload.obra_social_id,
        mes_periodo=payload.mes_periodo,
        anio_periodo=payload.anio_periodo,
        tipo="sin_factura",
        estado="A",
        total_debitos=Decimal("0"),
        total_creditos=Decimal("0"),
    )
    db.add(lote)
    await db.commit()
    await db.refresh(lote)
    return _lote_with_ajustes(lote)


# ================================================
# GET /snaps/por_os_periodo — Lista lotes por OS+período
# ================================================
# IMPORTANT: must be defined BEFORE /snaps/{lote_id} to avoid capture
@router.get("/snaps/por_os_periodo", response_model=List[LoteAjusteRead])
async def listar_lotes_por_os_periodo(
    obra_social_id: int = Query(...),
    mes_periodo: int = Query(..., ge=1, le=12),
    anio_periodo: int = Query(..., ge=1900, le=3000),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(LoteAjuste)
        .options(selectinload(LoteAjuste.ajustes))
        .where(
            LoteAjuste.obra_social_id == obra_social_id,
            LoteAjuste.mes_periodo == mes_periodo,
            LoteAjuste.anio_periodo == anio_periodo,
        )
        .order_by(LoteAjuste.created_at)
    )
    lotes = (await db.execute(stmt)).scalars().all()
    result = []
    for l in lotes:
        rows = await _get_enriched_ajuste_rows(db, l.id)
        result.append(_lote_with_ajustes(l, rows))
    return result


# ================================================
# GET /snaps/lista — Listado enriquecido (con OS + nro_factura)
# ================================================
@router.get("/snaps/lista", response_model=List[LoteListaRow])
async def listar_lotes_enriquecidos(
    tipo: Optional[str] = Query(None, description="normal | refacturacion"),
    mes: Optional[int] = Query(None, ge=1, le=12),
    anio: Optional[int] = Query(None, ge=1900),
    obra_social_id: Optional[int] = Query(None),
    estado: Optional[str] = Query(None, description="A | C | L"),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(
            LoteAjuste.id,
            LoteAjuste.tipo,
            LoteAjuste.estado,
            LoteAjuste.mes_periodo,
            LoteAjuste.anio_periodo,
            LoteAjuste.pago_id,
            LoteAjuste.obra_social_id,
            LoteAjuste.total_debitos,
            LoteAjuste.total_creditos,
            ObrasSociales.OBRA_SOCIAL.label("obra_social_nombre"),
            Periodos.NRO_FACT_1.label("nro_fact_1"),
            Periodos.NRO_FACT_2.label("nro_fact_2"),
        )
        .join(ObrasSociales, ObrasSociales.NRO_OBRASOCIAL == LoteAjuste.obra_social_id)
        .outerjoin(
            Periodos,
            (Periodos.NRO_OBRA_SOCIAL == LoteAjuste.obra_social_id)
            & (Periodos.MES == LoteAjuste.mes_periodo)
            & (Periodos.ANIO == LoteAjuste.anio_periodo),
        )
    )

    if tipo:
        stmt = stmt.where(LoteAjuste.tipo == tipo)
    if mes is not None:
        stmt = stmt.where(LoteAjuste.mes_periodo == mes)
    if anio is not None:
        stmt = stmt.where(LoteAjuste.anio_periodo == anio)
    if obra_social_id is not None:
        stmt = stmt.where(LoteAjuste.obra_social_id == obra_social_id)
    if estado:
        stmt = stmt.where(LoteAjuste.estado == estado)

    stmt = stmt.order_by(
        LoteAjuste.anio_periodo.desc(),
        LoteAjuste.mes_periodo.desc(),
        LoteAjuste.id,
    )

    rows = (await db.execute(stmt)).all()

    result = []
    for row in rows:
        nro_factura = None
        if row.nro_fact_1 and row.nro_fact_2:
            nro_factura = f"{row.nro_fact_1}-{row.nro_fact_2}"
        result.append(LoteListaRow(
            id=row.id,
            tipo=row.tipo,
            estado=row.estado,
            mes_periodo=row.mes_periodo,
            anio_periodo=row.anio_periodo,
            pago_id=row.pago_id,
            obra_social_id=row.obra_social_id,
            obra_social_nombre=row.obra_social_nombre,
            nro_factura=nro_factura,
            total_debitos=row.total_debitos,
            total_creditos=row.total_creditos,
        ))

    return result


# ================================================
# GET /snaps/buscar_atenciones — Buscar en guardar_atencion
# ================================================
@router.get("/snaps/buscar_atenciones", response_model=List[AtencionSearchRow])
async def buscar_atenciones(
    obra_social_id: int = Query(...),
    mes_periodo: int = Query(..., ge=1, le=12),
    anio_periodo: int = Query(..., ge=1900, le=3000),
    q: Optional[str] = Query(None, description="Buscar por NOMBRE_PRESTADOR, NRO_SOCIO o NRO_CONSULTA"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """
    Busca prestaciones en guardar_atencion por OS + período.
    El parámetro `q` filtra opcionalmente por nombre del prestador, nro socio o nro de orden/consulta.
    """
    stmt = select(GuardarAtencion).where(
        GuardarAtencion.NRO_OBRA_SOCIAL == obra_social_id,
        GuardarAtencion.MES_PERIODO == mes_periodo,
        GuardarAtencion.ANIO_PERIODO == anio_periodo,
        GuardarAtencion.EXISTE == "S",
    )

    if q and q.strip():
        term = f"%{q.strip()}%"
        from sqlalchemy import or_, cast
        from sqlalchemy import String as SAString
        stmt = stmt.where(
            or_(
                GuardarAtencion.NOMBRE_PRESTADOR.ilike(term),
                cast(GuardarAtencion.NRO_SOCIO, SAString).like(term),
                GuardarAtencion.NRO_CONSULTA.like(term),
            )
        )

    stmt = stmt.order_by(GuardarAtencion.FECHA_PRESTACION.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()

    return [
        AtencionSearchRow(
            id=r.ID,
            nro_socio=r.NRO_SOCIO,
            nombre_prestador=r.NOMBRE_PRESTADOR,
            nombre_afiliado=r.NOMBRE_AFILIADO,
            nro_consulta=r.NRO_CONSULTA,
            codigo_prestacion=r.CODIGO_PRESTACION,
            fecha_prestacion=str(r.FECHA_PRESTACION) if r.FECHA_PRESTACION else None,
            valor_cirujia=r.VALOR_CIRUJIA,
            mes_periodo=r.MES_PERIODO,
            anio_periodo=r.ANIO_PERIODO,
            nro_obra_social=r.NRO_OBRA_SOCIAL,
        )
        for r in rows
    ]


# ================================================
# GET /snaps/{lote_id} — Detalle del lote
# ================================================
@router.get("/snaps/{lote_id}", response_model=LoteAjusteRead)
async def obtener_lote(lote_id: int, db: AsyncSession = Depends(get_db)):
    lote = await _get_lote_with_ajustes(db, lote_id)
    rows = await _get_enriched_ajuste_rows(db, lote_id)
    return _lote_with_ajustes(lote, rows)


# ================================================
# PATCH /snaps/{lote_id}/estado — Cambiar estado (unificado)
# ================================================
@router.patch("/snaps/{lote_id}/estado", response_model=LoteAjusteRead)
async def cambiar_estado_lote(
    lote_id: int,
    payload: LoteCambiarEstadoPayload,
    db: AsyncSession = Depends(get_db),
):
    """
    Transiciones válidas:
      A → C : cerrar
      C → A : reabrir
      C → L : pasar al pago abierto (asigna pago_id automáticamente)
      L → C : quitar del pago (limpia pago_id)

    Tras cualquier cambio que afecte un pago recalcula los totales de las
    liquidaciones de esa OS+período en el pago involucrado.
    """
    lote = await _get_lote_with_ajustes(db, lote_id)
    nuevo = payload.estado

    if lote.estado == nuevo:
        raise HTTPException(409, f"El lote ya está en estado '{nuevo}'")

    transicion = (lote.estado, nuevo)
    pago_id_afectado: Optional[int] = None

    if transicion == ("A", "C"):
        lote.estado = "C"

    elif transicion == ("C", "A"):
        lote.estado = "A"

    elif transicion == ("C", "L"):
        pago_abierto = (await db.execute(
            select(Pago).where(Pago.estado == "A").limit(1)
        )).scalars().first()
        if not pago_abierto:
            raise HTTPException(409, "No hay pago abierto al que asignar el lote")
        lote.pago_id = pago_abierto.id
        lote.estado = "L"
        pago_id_afectado = pago_abierto.id

    elif transicion == ("L", "C"):
        pago_id_afectado = lote.pago_id
        pago = await db.get(Pago, pago_id_afectado)
        if pago and pago.estado == "C":
            raise HTTPException(409, "No se puede quitar un lote de un pago cerrado")
        lote.pago_id = None
        lote.estado = "C"

    else:
        if lote.estado == "AP":
            raise HTTPException(
                409,
                "El lote está en estado 'AP' (Aplicado) y es inmutable. "
                "Solo puede volver a 'C' si el pago al que pertenece es eliminado."
            )
        raise HTTPException(
            409,
            f"Transición '{lote.estado}' → '{nuevo}' no permitida. "
            "Válidas: A→C, C→A, C→L, L→C"
        )

    # Recalcular totales de liquidaciones afectadas
    if pago_id_afectado:
        liqs = (await db.execute(
            select(Liquidacion).where(
                Liquidacion.pago_id == pago_id_afectado,
                Liquidacion.obra_social_id == lote.obra_social_id,
                Liquidacion.mes_periodo == lote.mes_periodo,
                Liquidacion.anio_periodo == lote.anio_periodo,
            )
        )).scalars().all()
        for liq in liqs:
            await recalcular_totales_de_liquidacion(db, liq.id)

    await db.commit()
    lote_ret = await _get_lote_with_ajustes(db, lote_id)
    rows = await _get_enriched_ajuste_rows(db, lote_id)
    return _lote_with_ajustes(lote_ret, rows)


# ================================================
# DELETE /snaps/{lote_id} — Eliminar lote
# ================================================
@router.delete("/snaps/{lote_id}", status_code=204)
async def eliminar_lote(lote_id: int, db: AsyncSession = Depends(get_db)):
    """409 si el lote está en estado 'L' (en pago abierto) o 'AP' (aplicado — inmutable)."""
    lote = await db.get(LoteAjuste, lote_id)
    if not lote:
        raise HTTPException(404, "Lote no encontrado")
    if lote.estado == "L":
        raise HTTPException(
            409,
            "No se puede eliminar un lote que está en un pago (estado='L'). "
            "Cambiá el estado a 'C' primero."
        )
    if lote.estado == "AP":
        raise HTTPException(
            409,
            "No se puede eliminar un lote aplicado (estado='AP'). "
            "Pertenece a un pago cerrado. Solo se libera si el pago es eliminado."
        )
    await db.delete(lote)
    await db.commit()
    return None


# ================================================
# POST /snaps/{lote_id}/items — Crear ajuste
# ================================================
@router.post("/snaps/{lote_id}/items", response_model=AjusteRead, status_code=201)
async def crear_ajuste(
    lote_id: int,
    payload: AjusteCreate,
    db: AsyncSession = Depends(get_db),
):
    lote = await db.get(LoteAjuste, lote_id)
    if not lote:
        raise HTTPException(404, "Lote no encontrado")
    if lote.estado != "A":
        raise HTTPException(409, f"No se puede agregar ajustes a un lote en estado '{lote.estado}'")

    medico_id = payload.medico_id
    obra_social_id = lote.obra_social_id

    if lote.tipo == "sin_factura":
        # Lotes sin factura: medico_id siempre requerido; id_atencion no aplica
        if medico_id is None:
            raise HTTPException(422, "Para lotes sin_factura se requiere medico_id explícito")
    else:
        # Si se provee id_atencion sin medico_id, derivar medico_id y obra_social_id desde guardar_atencion
        if medico_id is None:
            if payload.id_atencion is None:
                raise HTTPException(422, "Se requiere medico_id o id_atencion")
            atencion = await db.get(GuardarAtencion, payload.id_atencion)
            if not atencion:
                raise HTTPException(404, f"Atención {payload.id_atencion} no encontrada")
            medico_row = (await db.execute(
                select(ListadoMedico.ID).where(ListadoMedico.NRO_SOCIO == atencion.NRO_SOCIO).limit(1)
            )).scalar_one_or_none()
            if medico_row is None:
                raise HTTPException(404, f"No se encontró médico con NRO_SOCIO={atencion.NRO_SOCIO}")
            medico_id = medico_row
            obra_social_id = atencion.NRO_OBRA_SOCIAL

    ajuste = Ajuste(
        lote_id=lote_id,
        tipo=payload.tipo,
        medico_id=medico_id,
        obra_social_id=obra_social_id,
        honorarios=payload.honorarios,
        gastos=payload.gastos,
        observacion=payload.observacion,
        id_atencion=payload.id_atencion,
        origen="manual",
    )
    db.add(ajuste)
    await db.flush()
    await recalcular_totales_lote(db, lote_id)
    await db.commit()
    await db.refresh(ajuste)
    atencion = await db.get(GuardarAtencion, ajuste.id_atencion) if ajuste.id_atencion else None
    medico = await db.get(ListadoMedico, ajuste.medico_id) if not atencion else None
    return _build_ajuste_read(ajuste, atencion, medico)


# ================================================
# PUT /snaps/{lote_id}/items/{ajuste_id} — Actualizar ajuste
# ================================================
@router.put("/snaps/{lote_id}/items/{ajuste_id}", response_model=AjusteRead)
async def actualizar_ajuste(
    lote_id: int,
    ajuste_id: int,
    payload: AjusteUpdate,
    db: AsyncSession = Depends(get_db),
):
    lote = await db.get(LoteAjuste, lote_id)
    if not lote:
        raise HTTPException(404, "Lote no encontrado")
    if lote.estado != "A":
        raise HTTPException(409, f"No se puede editar ajustes en un lote en estado '{lote.estado}'")

    ajuste = (await db.execute(
        select(Ajuste).where(Ajuste.id == ajuste_id, Ajuste.lote_id == lote_id)
    )).scalars().first()
    if not ajuste:
        raise HTTPException(404, "Ajuste no encontrado en ese lote")

    if payload.tipo is not None:
        ajuste.tipo = payload.tipo
    if payload.honorarios is not None:
        ajuste.honorarios = payload.honorarios
    if payload.gastos is not None:
        ajuste.gastos = payload.gastos
    if payload.observacion is not None:
        ajuste.observacion = payload.observacion

    await db.flush()
    await recalcular_totales_lote(db, lote_id)
    await db.commit()
    await db.refresh(ajuste)
    atencion = await db.get(GuardarAtencion, ajuste.id_atencion) if ajuste.id_atencion else None
    medico = await db.get(ListadoMedico, ajuste.medico_id) if not atencion else None
    return _build_ajuste_read(ajuste, atencion, medico)


# ================================================
# DELETE /snaps/{lote_id}/items/{ajuste_id} — Eliminar ajuste
# ================================================
@router.delete("/snaps/{lote_id}/items/{ajuste_id}", status_code=204)
async def eliminar_ajuste(
    lote_id: int,
    ajuste_id: int,
    db: AsyncSession = Depends(get_db),
):
    lote = await db.get(LoteAjuste, lote_id)
    if not lote:
        raise HTTPException(404, "Lote no encontrado")
    if lote.estado != "A":
        raise HTTPException(409, f"No se puede eliminar ajustes de un lote en estado '{lote.estado}'")

    ajuste = (await db.execute(
        select(Ajuste).where(Ajuste.id == ajuste_id, Ajuste.lote_id == lote_id)
    )).scalars().first()
    if not ajuste:
        raise HTTPException(404, "Ajuste no encontrado en ese lote")

    await db.delete(ajuste)
    await db.flush()
    await recalcular_totales_lote(db, lote_id)
    await db.commit()
    return None
