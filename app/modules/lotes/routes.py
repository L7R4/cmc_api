from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, case, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.database import get_db
from app.db.models import (
    Ajuste,
    DetalleFacturacionCMC,
    FacturacionCMC,
    Liquidacion,
    LoteAjuste,
    Pago,
)
from app.db.models.catalogs import ObrasSociales
from app.db.models.medico import ListadoMedico
from app.modules.liquidacion.service import recalcular_totales_de_liquidacion
from app.modules.deducciones.service import generar_y_recalcular_porcentuales
from app.modules.lotes.schemas import (
    AjusteCreate,
    AjusteRead,
    AjusteUpdate,
    LoteAjusteCreate,
    LoteAjusteRead,
    LoteCambiarEstadoPayload,
    LoteListaRow,
    LoteRefacturacionCreate,
    LoteSinFacturaCreate,
    PrestacionCMCSearchRow,
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


def _build_ajuste_read(ajuste: Ajuste, cmc_det=None, medico=None) -> AjusteRead:
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
        nombre_afiliado=cmc_det.nom_ape_p if cmc_det else None,
        nombre_prestador=medico.NOMBRE if medico else None,
        nro_socio=medico.NRO_SOCIO if medico else None,
        nro_consulta=cmc_det.nro_orden if cmc_det else None,
        tpo_funcion=cmc_det.tpo_funcion if cmc_det else None,
        codigo_prestacion=cmc_det.cod_nom if cmc_det else None,
        fecha_prestacion=str(cmc_det.fecha_practica) if cmc_det and cmc_det.fecha_practica else None,
    )


def _lote_with_ajustes(lote: LoteAjuste, ajuste_rows=None) -> LoteAjusteRead:
    if ajuste_rows is not None:
        ajustes = [_build_ajuste_read(a, cmc, med) for a, cmc, med in ajuste_rows]
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
    """Devuelve lista de (Ajuste, DetalleFacturacionCMC | None, ListadoMedico | None) para un lote."""
    stmt = (
        select(Ajuste, DetalleFacturacionCMC, ListadoMedico)
        .outerjoin(DetalleFacturacionCMC,
                   DetalleFacturacionCMC.id_detalle_prestaciones == Ajuste.id_atencion)
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
    """Crea un lote tipo='sin_factura'. Los ajustes deben incluir medico_id explícito."""
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
# GET /snaps/lista — Listado enriquecido (con OS + nro_factura desde FacturacionCMC)
# ================================================
@router.get("/snaps/lista", response_model=List[LoteListaRow])
async def listar_lotes_enriquecidos(
    tipo: Optional[str] = Query(None, description="normal | refacturacion | sin_factura"),
    mes: Optional[int] = Query(None, ge=1, le=12),
    anio: Optional[int] = Query(None, ge=1900),
    obra_social_id: Optional[int] = Query(None),
    estado: Optional[str] = Query(None, description="A | C | L"),
    db: AsyncSession = Depends(get_db),
):
    periodo_expr = func.concat(
        LoteAjuste.anio_periodo,
        func.lpad(cast(LoteAjuste.mes_periodo, String), 2, "0"),
    )
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
            FacturacionCMC.nro_factura.label("nro_factura"),
        )
        .join(ObrasSociales, ObrasSociales.NRO_OBRASOCIAL == LoteAjuste.obra_social_id)
        .outerjoin(
            FacturacionCMC,
            (cast(FacturacionCMC.cod_obr, String) == cast(LoteAjuste.obra_social_id, String))
            & (FacturacionCMC.periodo == periodo_expr)
            & (FacturacionCMC.estado.in_(["L", "LC"])),
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

    return [
        LoteListaRow(
            id=row.id,
            tipo=row.tipo,
            estado=row.estado,
            mes_periodo=row.mes_periodo,
            anio_periodo=row.anio_periodo,
            pago_id=row.pago_id,
            obra_social_id=row.obra_social_id,
            obra_social_nombre=row.obra_social_nombre,
            nro_factura=row.nro_factura,
            total_debitos=row.total_debitos,
            total_creditos=row.total_creditos,
        )
        for row in rows
    ]


# ================================================
# GET /snaps/buscar_atenciones — Buscar en detalle_facturacion (CMC)
# ================================================
@router.get("/snaps/buscar_atenciones", response_model=List[PrestacionCMCSearchRow])
async def buscar_atenciones(
    obra_social_id: int = Query(...),
    mes_periodo: int = Query(..., ge=1, le=12),
    anio_periodo: int = Query(..., ge=1900, le=3000),
    q: Optional[str] = Query(None, description="Buscar por cod_med, nom_ape_p o cod_nom"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Busca prestaciones en detalle_facturacion (CMC) por OS + período."""
    periodo = f"{anio_periodo}{mes_periodo:02d}"
    cod_obr = str(obra_social_id)

    stmt = (
        select(DetalleFacturacionCMC, ListadoMedico.NOMBRE.label("medico_nombre"))
        .outerjoin(
            ListadoMedico,
            ListadoMedico.NRO_SOCIO == cast(DetalleFacturacionCMC.cod_med, String),
        )
        .where(
            DetalleFacturacionCMC.cod_obr == cod_obr,
            DetalleFacturacionCMC.periodo == periodo,
            DetalleFacturacionCMC.estado == "L",
        )
    )

    if q and q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                DetalleFacturacionCMC.cod_med.like(term),
                DetalleFacturacionCMC.nom_ape_p.ilike(term),
                DetalleFacturacionCMC.cod_nom.like(term),
            )
        )

    stmt = stmt.order_by(DetalleFacturacionCMC.fecha_practica.desc()).limit(limit)
    rows = (await db.execute(stmt)).all()

    return [
        PrestacionCMCSearchRow(
            id=r.DetalleFacturacionCMC.id_detalle_prestaciones,
            cod_med=r.DetalleFacturacionCMC.cod_med,
            medico_nombre=r.medico_nombre,
            nom_ape_p=r.DetalleFacturacionCMC.nom_ape_p,
            cod_nom=r.DetalleFacturacionCMC.cod_nom,
            tpo_funcion=r.DetalleFacturacionCMC.tpo_funcion,
            fecha_practica=str(r.DetalleFacturacionCMC.fecha_practica) if r.DetalleFacturacionCMC.fecha_practica else None,
            nro_orden=r.DetalleFacturacionCMC.nro_orden,
            honorarios=r.DetalleFacturacionCMC.honorarios,
            gastos=r.DetalleFacturacionCMC.gastos,
            ayudante=r.DetalleFacturacionCMC.ayudante,
            importe_total=r.DetalleFacturacionCMC.importe_total,
            periodo=r.DetalleFacturacionCMC.periodo,
            cod_obr=r.DetalleFacturacionCMC.cod_obr,
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
        await generar_y_recalcular_porcentuales(db, pago_id_afectado)

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
        if medico_id is None:
            raise HTTPException(422, "Para lotes sin_factura se requiere medico_id explícito")
    else:
        if medico_id is None:
            if payload.id_atencion is None:
                raise HTTPException(422, "Se requiere medico_id o id_atencion")
            cmc_det = await db.get(DetalleFacturacionCMC, payload.id_atencion)
            if not cmc_det:
                raise HTTPException(404, f"Detalle CMC {payload.id_atencion} no encontrado")
            medico_row = (await db.execute(
                select(ListadoMedico.ID).where(
                    ListadoMedico.NRO_SOCIO == int(cmc_det.cod_med)
                ).limit(1)
            )).scalar_one_or_none()
            if medico_row is None:
                raise HTTPException(404, f"Médico con cod_med={cmc_det.cod_med} no encontrado")
            medico_id = medico_row

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
    cmc_det = await db.get(DetalleFacturacionCMC, ajuste.id_atencion) if ajuste.id_atencion else None
    medico = await db.get(ListadoMedico, ajuste.medico_id)
    return _build_ajuste_read(ajuste, cmc_det, medico)


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
    cmc_det = await db.get(DetalleFacturacionCMC, ajuste.id_atencion) if ajuste.id_atencion else None
    medico = await db.get(ListadoMedico, ajuste.medico_id)
    return _build_ajuste_read(ajuste, cmc_det, medico)


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
