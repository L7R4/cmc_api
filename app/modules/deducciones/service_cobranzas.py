"""Panel de Cobranzas: deuda agregada por concepto, solo lectura.

No reescribe la máquina de estados de `service.py` — agrega sobre la misma
tabla `deducciones` con la misma definición de deuda que usa
`get_top_deudores()` (`service.py:727`), ampliada con el corte de período
(excluye cuotas futuras por defecto) y con `paga_por_caja` como dimensión
propia, porque acá SÍ importa distinguir la deuda de ventanilla de la de
liquidación.

Definición de deuda usada en todo el módulo:

    estado IN ('pendiente', 'en_pago')
    AND (calculado_total - monto_aplicado) > 0
    AND (anio_aplicar, mes_aplicar) <= (hoy)   -- salvo incluir_futuros=True

Toda la agregación se hace en SQL (GROUP BY / SUM / COUNT), nunca en Python
sobre las 130k filas de `deducciones` — es el error que ya tiene
`fetch_deducciones_item` (`service.py:515`).
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Deduccion, Descuentos, ListadoMedico
from app.modules.deducciones.helpers import TWOPLACES
from app.modules.deducciones.schemas import (
    CobranzaCuotaItem,
    CobranzaExportRow,
    CobranzaMedicoDeudorItem,
    CobranzaMedicoDetalle,
    CobranzaPorConceptoItem,
    CobranzasResumen,
    CobranzaSocioDeudorItem,
)

SALDO_EXPR = Deduccion.calculado_total - Deduccion.monto_aplicado


@dataclass
class CobranzasFiltros:
    q: Optional[str] = None
    mes_desde: Optional[int] = None
    anio_desde: Optional[int] = None
    mes_hasta: Optional[int] = None
    anio_hasta: Optional[int] = None
    paga_por_caja: Optional[bool] = None
    incluir_futuros: bool = False
    saldo_min: Optional[Decimal] = None
    # Acota todo a un concepto. Lo usa la pestaña "Por concepto" al abrir el
    # detalle de un socio: ahí interesa su estado EN ESE concepto, no la deuda
    # completa (que es lo que muestra la pestaña "Por socio").
    descuento_id: Optional[int] = None


def _periodo_expr():
    """Fecha del primer día del período, para comparar y ordenar por antigüedad."""
    return func.str_to_date(
        func.concat(Deduccion.anio_aplicar, "-", Deduccion.mes_aplicar, "-01"),
        "%Y-%m-%d",
        type_=sa.Date,
    )


def _condiciones_deuda(filtros: CobranzasFiltros) -> list:
    conds = [
        Deduccion.estado.in_(["pendiente", "en_pago"]),
        SALDO_EXPR > 0,
    ]
    if not filtros.incluir_futuros:
        hoy = date.today()
        conds.append(
            or_(
                Deduccion.anio_aplicar < hoy.year,
                and_(Deduccion.anio_aplicar == hoy.year, Deduccion.mes_aplicar <= hoy.month),
            )
        )
    if filtros.mes_desde is not None and filtros.anio_desde is not None:
        conds.append(
            or_(
                Deduccion.anio_aplicar > filtros.anio_desde,
                and_(Deduccion.anio_aplicar == filtros.anio_desde, Deduccion.mes_aplicar >= filtros.mes_desde),
            )
        )
    if filtros.mes_hasta is not None and filtros.anio_hasta is not None:
        conds.append(
            or_(
                Deduccion.anio_aplicar < filtros.anio_hasta,
                and_(Deduccion.anio_aplicar == filtros.anio_hasta, Deduccion.mes_aplicar <= filtros.mes_hasta),
            )
        )
    if filtros.paga_por_caja is not None:
        conds.append(Deduccion.paga_por_caja == filtros.paga_por_caja)
    if filtros.descuento_id is not None:
        conds.append(Deduccion.descuento_id == filtros.descuento_id)
    if filtros.saldo_min is not None:
        # A nivel de cuota, no de agregado: "ignorar cuotas menores a $X",
        # misma semántica en resumen/por_concepto/medicos/export.
        conds.append(SALDO_EXPR >= filtros.saldo_min)
    return conds


def _formatear_periodo(valor) -> Optional[str]:
    if valor is None:
        return None
    if isinstance(valor, date):
        return f"{valor.month:02d}/{valor.year}"
    return None


def _meses_atraso(periodo_min) -> int:
    if periodo_min is None:
        return 0
    hoy = date.today()
    return max(0, (hoy.year - periodo_min.year) * 12 + (hoy.month - periodo_min.month))


# ── Resumen (KPIs) ───────────────────────────────────────────────────────────

async def get_resumen(db: AsyncSession, filtros: CobranzasFiltros) -> CobranzasResumen:
    conds = _condiciones_deuda(filtros)
    q = select(
        func.coalesce(func.sum(SALDO_EXPR), 0).label("saldo_total"),
        func.coalesce(
            func.sum(case((Deduccion.paga_por_caja == False, SALDO_EXPR), else_=0)), 0
        ).label("saldo_liquidacion"),
        func.coalesce(
            func.sum(case((Deduccion.paga_por_caja == True, SALDO_EXPR), else_=0)), 0
        ).label("saldo_caja"),
        func.count(func.distinct(Deduccion.medico_id)).label("medicos_con_deuda"),
        func.count(func.distinct(Deduccion.descuento_id)).label("conceptos_con_deuda"),
        func.count(Deduccion.id).label("cuotas_impagas"),
    ).select_from(Deduccion)

    if filtros.q:
        q = q.join(Descuentos, Descuentos.id == Deduccion.descuento_id).join(
            ListadoMedico, ListadoMedico.ID == Deduccion.medico_id
        )
        like = f"%{filtros.q}%"
        conds.append(
            or_(
                Descuentos.nombre.ilike(like),
                func.cast(Descuentos.nro_colegio, sa.String(20)).like(like),
                ListadoMedico.NOMBRE.ilike(like),
                func.cast(ListadoMedico.NRO_SOCIO, sa.String(20)).like(like),
            )
        )

    row = (await db.execute(q.where(*conds))).one()
    return CobranzasResumen(
        saldo_total=Decimal(str(row.saldo_total or 0)).quantize(TWOPLACES),
        saldo_liquidacion=Decimal(str(row.saldo_liquidacion or 0)).quantize(TWOPLACES),
        saldo_caja=Decimal(str(row.saldo_caja or 0)).quantize(TWOPLACES),
        medicos_con_deuda=int(row.medicos_con_deuda or 0),
        conceptos_con_deuda=int(row.conceptos_con_deuda or 0),
        cuotas_impagas=int(row.cuotas_impagas or 0),
    )


# ── Por concepto ─────────────────────────────────────────────────────────────

async def get_por_concepto(
    db: AsyncSession, filtros: CobranzasFiltros
) -> list[CobranzaPorConceptoItem]:
    conds = _condiciones_deuda(filtros)

    q = (
        select(
            Descuentos.id.label("descuento_id"),
            Descuentos.nro_colegio,
            Descuentos.nombre,
            func.count(func.distinct(Deduccion.medico_id)).label("medicos_con_deuda"),
            func.count(Deduccion.id).label("cuotas_impagas"),
            func.sum(SALDO_EXPR).label("saldo"),
            func.sum(
                case((Deduccion.paga_por_caja == True, SALDO_EXPR), else_=0)
            ).label("saldo_caja"),
            func.min(_periodo_expr()).label("periodo_mas_antiguo"),
        )
        .select_from(Deduccion)
        .join(Descuentos, Descuentos.id == Deduccion.descuento_id)
        .where(*conds)
    )
    if filtros.q:
        like = f"%{filtros.q}%"
        q = q.where(
            or_(
                Descuentos.nombre.ilike(like),
                func.cast(Descuentos.nro_colegio, sa.String(20)).like(like),
            )
        )
    q = q.group_by(Descuentos.id, Descuentos.nro_colegio, Descuentos.nombre)
    q = q.order_by(func.sum(SALDO_EXPR).desc())

    rows = (await db.execute(q)).all()
    return [
        CobranzaPorConceptoItem(
            descuento_id=int(r.descuento_id),
            nro_colegio=int(r.nro_colegio),
            nombre=r.nombre,
            medicos_con_deuda=int(r.medicos_con_deuda or 0),
            cuotas_impagas=int(r.cuotas_impagas or 0),
            saldo=Decimal(str(r.saldo or 0)).quantize(TWOPLACES),
            saldo_caja=Decimal(str(r.saldo_caja or 0)).quantize(TWOPLACES),
            periodo_mas_antiguo=_formatear_periodo(r.periodo_mas_antiguo),
        )
        for r in rows
    ]


# ── Médicos deudores de un concepto ──────────────────────────────────────────

async def get_medicos_por_concepto(
    db: AsyncSession,
    descuento_id: int,
    filtros: CobranzasFiltros,
    page: int,
    size: int,
) -> tuple[Optional[str], int, list[CobranzaMedicoDeudorItem]]:
    descuento_nombre = await db.scalar(select(Descuentos.nombre).where(Descuentos.id == descuento_id))
    if descuento_nombre is None:
        return None, 0, []

    conds = _condiciones_deuda(filtros)
    conds.append(Deduccion.descuento_id == descuento_id)

    pagador = ListadoMedico.__table__.alias("pagador_medico")

    base = (
        select(
            Deduccion.medico_id,
            ListadoMedico.NRO_SOCIO.label("nro_socio"),
            ListadoMedico.NOMBRE.label("medico_nombre"),
            func.count(Deduccion.id).label("cuotas_impagas"),
            func.min(_periodo_expr()).label("periodo_mas_antiguo"),
            func.sum(SALDO_EXPR).label("saldo"),
            func.max(Deduccion.paga_por_caja).label("paga_por_caja"),
            func.max(Deduccion.pagador_medico_id).label("pagador_medico_id"),
        )
        .select_from(Deduccion)
        .join(ListadoMedico, ListadoMedico.ID == Deduccion.medico_id)
        .where(*conds)
    )
    if filtros.q:
        like = f"%{filtros.q}%"
        base = base.where(
            or_(
                ListadoMedico.NOMBRE.ilike(like),
                func.cast(ListadoMedico.NRO_SOCIO, sa.String(20)).like(like),
            )
        )
    base = base.group_by(Deduccion.medico_id, ListadoMedico.NRO_SOCIO, ListadoMedico.NOMBRE)

    subq = base.subquery()
    total = await db.scalar(select(func.count()).select_from(subq))

    rows_q = (
        select(subq, pagador.c.NOMBRE.label("pagador_nombre"))
        .select_from(subq)
        .outerjoin(pagador, pagador.c.ID == subq.c.pagador_medico_id)
        .order_by(subq.c.saldo.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    rows = (await db.execute(rows_q)).all()

    items = [
        CobranzaMedicoDeudorItem(
            medico_id=int(r.medico_id),
            nro_socio=int(r.nro_socio),
            medico_nombre=r.medico_nombre or "",
            cuotas_impagas=int(r.cuotas_impagas or 0),
            periodo_mas_antiguo=_formatear_periodo(r.periodo_mas_antiguo),
            meses_atraso=_meses_atraso(r.periodo_mas_antiguo),
            saldo=Decimal(str(r.saldo or 0)).quantize(TWOPLACES),
            paga_por_caja=bool(r.paga_por_caja),
            pagador_medico_id=int(r.pagador_medico_id) if r.pagador_medico_id else None,
            pagador_nombre=r.pagador_nombre,
        )
        for r in rows
    ]
    return descuento_nombre, int(total or 0), items


# ── Socios deudores (pestaña "Por socio") ────────────────────────────────────

async def get_socios_deudores(
    db: AsyncSession,
    filtros: CobranzasFiltros,
    page: int,
    size: int,
) -> tuple[int, list[CobranzaSocioDeudorItem]]:
    """Un renglón por médico con su deuda consolidada de TODOS los conceptos.

    Es la contracara de `get_por_concepto`: mismo universo de deuda, agrupado
    por médico en vez de por concepto. La suma de `saldo` de todas las páginas
    da el mismo total que `get_resumen().saldo_total`.
    """
    conds = _condiciones_deuda(filtros)

    base = (
        select(
            Deduccion.medico_id,
            ListadoMedico.NRO_SOCIO.label("nro_socio"),
            ListadoMedico.NOMBRE.label("medico_nombre"),
            func.count(func.distinct(Deduccion.descuento_id)).label("conceptos_con_deuda"),
            func.count(Deduccion.id).label("cuotas_impagas"),
            func.min(_periodo_expr()).label("periodo_mas_antiguo"),
            func.sum(SALDO_EXPR).label("saldo"),
            func.sum(
                case((Deduccion.paga_por_caja == True, SALDO_EXPR), else_=0)
            ).label("saldo_caja"),
        )
        .select_from(Deduccion)
        .join(ListadoMedico, ListadoMedico.ID == Deduccion.medico_id)
        .where(*conds)
    )
    if filtros.q:
        like = f"%{filtros.q}%"
        base = base.where(
            or_(
                ListadoMedico.NOMBRE.ilike(like),
                func.cast(ListadoMedico.NRO_SOCIO, sa.String(20)).like(like),
            )
        )
    base = base.group_by(Deduccion.medico_id, ListadoMedico.NRO_SOCIO, ListadoMedico.NOMBRE)

    subq = base.subquery()
    total = await db.scalar(select(func.count()).select_from(subq))

    rows = (
        await db.execute(
            select(subq).order_by(subq.c.saldo.desc()).offset((page - 1) * size).limit(size)
        )
    ).all()

    items = [
        CobranzaSocioDeudorItem(
            medico_id=int(r.medico_id),
            nro_socio=int(r.nro_socio),
            medico_nombre=r.medico_nombre or "",
            conceptos_con_deuda=int(r.conceptos_con_deuda or 0),
            cuotas_impagas=int(r.cuotas_impagas or 0),
            periodo_mas_antiguo=_formatear_periodo(r.periodo_mas_antiguo),
            meses_atraso=_meses_atraso(r.periodo_mas_antiguo),
            saldo=Decimal(str(r.saldo or 0)).quantize(TWOPLACES),
            saldo_caja=Decimal(str(r.saldo_caja or 0)).quantize(TWOPLACES),
        )
        for r in rows
    ]
    return int(total or 0), items


# ── Detalle de un médico ─────────────────────────────────────────────────────

async def get_detalle_medico(
    db: AsyncSession, medico_id: int, filtros: CobranzasFiltros
) -> Optional[CobranzaMedicoDetalle]:
    medico = (
        await db.execute(
            select(ListadoMedico.NRO_SOCIO, ListadoMedico.NOMBRE).where(ListadoMedico.ID == medico_id)
        )
    ).first()
    if medico is None:
        return None

    conds = _condiciones_deuda(filtros)
    conds.append(Deduccion.medico_id == medico_id)

    q = (
        select(
            Deduccion.id.label("deduccion_id"),
            Deduccion.descuento_id,
            Descuentos.nombre.label("descuento_nombre"),
            Descuentos.nro_colegio,
            Deduccion.mes_aplicar,
            Deduccion.anio_aplicar,
            Deduccion.calculado_total,
            Deduccion.monto_aplicado,
            Deduccion.paga_por_caja,
            Deduccion.estado,
        )
        .select_from(Deduccion)
        .join(Descuentos, Descuentos.id == Deduccion.descuento_id)
        .where(*conds)
        .order_by(Deduccion.anio_aplicar.asc(), Deduccion.mes_aplicar.asc(), Descuentos.nro_colegio.asc())
    )
    rows = (await db.execute(q)).all()

    cuotas = [
        CobranzaCuotaItem(
            deduccion_id=int(r.deduccion_id),
            descuento_id=int(r.descuento_id),
            descuento_nombre=r.descuento_nombre,
            nro_colegio=int(r.nro_colegio),
            mes_aplicar=r.mes_aplicar,
            anio_aplicar=r.anio_aplicar,
            calculado_total=Decimal(str(r.calculado_total)).quantize(TWOPLACES),
            monto_aplicado=Decimal(str(r.monto_aplicado)).quantize(TWOPLACES),
            saldo=Decimal(str(r.calculado_total - r.monto_aplicado)).quantize(TWOPLACES),
            paga_por_caja=bool(r.paga_por_caja),
            estado=r.estado,
        )
        for r in rows
    ]
    saldo_total = sum((c.saldo for c in cuotas), Decimal("0.00"))

    return CobranzaMedicoDetalle(
        medico_id=medico_id,
        nro_socio=int(medico.NRO_SOCIO),
        medico_nombre=medico.NOMBRE or "",
        saldo_total=saldo_total.quantize(TWOPLACES),
        cuotas=cuotas,
    )


# ── Export plano ─────────────────────────────────────────────────────────────

async def get_export_rows(db: AsyncSession, filtros: CobranzasFiltros) -> list[CobranzaExportRow]:
    conds = _condiciones_deuda(filtros)
    q = (
        select(
            Deduccion.medico_id,
            ListadoMedico.NRO_SOCIO.label("nro_socio"),
            ListadoMedico.NOMBRE.label("medico_nombre"),
            Deduccion.descuento_id,
            Descuentos.nro_colegio,
            Descuentos.nombre.label("descuento_nombre"),
            Deduccion.mes_aplicar,
            Deduccion.anio_aplicar,
            Deduccion.calculado_total,
            Deduccion.monto_aplicado,
            Deduccion.paga_por_caja,
            Deduccion.estado,
        )
        .select_from(Deduccion)
        .join(Descuentos, Descuentos.id == Deduccion.descuento_id)
        .join(ListadoMedico, ListadoMedico.ID == Deduccion.medico_id)
        .where(*conds)
    )
    if filtros.q:
        like = f"%{filtros.q}%"
        q = q.where(
            or_(
                Descuentos.nombre.ilike(like),
                func.cast(Descuentos.nro_colegio, sa.String(20)).like(like),
                ListadoMedico.NOMBRE.ilike(like),
                func.cast(ListadoMedico.NRO_SOCIO, sa.String(20)).like(like),
            )
        )
    q = q.order_by(Descuentos.nro_colegio.asc(), ListadoMedico.NOMBRE.asc())

    rows = (await db.execute(q)).all()
    return [
        CobranzaExportRow(
            medico_id=int(r.medico_id),
            nro_socio=int(r.nro_socio),
            medico_nombre=r.medico_nombre or "",
            descuento_id=int(r.descuento_id),
            nro_colegio=int(r.nro_colegio),
            descuento_nombre=r.descuento_nombre,
            mes_aplicar=r.mes_aplicar,
            anio_aplicar=r.anio_aplicar,
            calculado_total=Decimal(str(r.calculado_total)).quantize(TWOPLACES),
            monto_aplicado=Decimal(str(r.monto_aplicado)).quantize(TWOPLACES),
            saldo=Decimal(str(r.calculado_total - r.monto_aplicado)).quantize(TWOPLACES),
            paga_por_caja=bool(r.paga_por_caja),
            estado=r.estado,
        )
        for r in rows
    ]
