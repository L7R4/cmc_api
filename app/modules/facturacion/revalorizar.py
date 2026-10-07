"""Revalorizar: al cargar el precio de un código en una O.S., actualizar las
prestaciones ya cargadas de ese código.

Toma TODAS las automáticas (`manual = 'A'`) que siguen ABIERTAS (estado 'A'), no
sólo las cargadas en $0 sin precio (`sin_valorizar`): también las que quedaron con
un precio viejo o en $0 sin marca. El cálculo por fila es el de `recotizar.py`, el
mismo que usa el recálculo de factura — precio del médico a la fecha de la
práctica, concepto por concepto, porcentaje y coseguro sugerido.

Sólo se listan las que cambian (y las que no se pueden cotizar, con el motivo);
las que ya tienen ese precio se cuentan en `sin_cambios`. Las de períodos cerrados
y las manuales no se tocan. Con `dry_run` sólo informa antes/después.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.cmc_facturacion import DetalleFacturacionCMC
from app.modules.facturacion import recotizar


class RevalorizarIn(BaseModel):
    cod_obra: str
    codigo: str
    # None = todas las pendientes de ese código y O.S.
    ids: Optional[List[int]] = None
    dry_run: bool = False


class RevalorizarItem(BaseModel):
    id: int
    periodo: str
    cod_med: str
    fecha_practica: Optional[datetime.date] = None
    conceptos: str
    estado: Literal["revalorizada", "sin_precio", "omitida", "error"]
    motivo: Optional[str] = None
    importe_antes: Decimal = Decimal("0")
    honorarios: Decimal = Decimal("0")
    gastos: Decimal = Decimal("0")
    ayudante: Decimal = Decimal("0")
    coseguro: Decimal = Decimal("0")
    importe_despues: Decimal = Decimal("0")


class RevalorizarOut(BaseModel):
    dry_run: bool
    cod_obra: str
    codigo: str
    total: int
    revalorizadas: int
    # Automáticas abiertas que ya tienen ese precio (no se listan).
    sin_cambios: int = 0
    items: List[RevalorizarItem] = Field(default_factory=list)


async def pendientes(db: AsyncSession, cod_obra: str, codigo: str) -> list[DetalleFacturacionCMC]:
    D = DetalleFacturacionCMC
    return list((await db.execute(
        select(D).where(
            D.cod_obr == cod_obra, D.cod_nom == codigo, D.estado == "A", D.manual == "A",
        ).order_by(D.periodo, D.id_detalle_prestaciones)
    )).scalars())


async def contar_pendientes(db: AsyncSession, cod_obra: str, codigo: str) -> int:
    return len(await pendientes(db, cod_obra, codigo))


async def _revalorizar_fila(
    db: AsyncSession, row: DetalleFacturacionCMC, aplicar: bool,
    cache: Optional[recotizar.CacheCotizacion] = None,
) -> Optional[RevalorizarItem]:
    """Mismo cálculo que el recálculo de factura (`recotizar.recotizar_fila`).
    None = ya tiene ese precio."""
    base = dict(
        id=row.id_detalle_prestaciones, periodo=row.periodo, cod_med=str(row.cod_med),
        fecha_practica=row.fecha_practica, conceptos=recotizar.conceptos_de(row),
        importe_antes=row.importe_total or Decimal("0"),
    )
    r = await recotizar.recotizar_fila(db, row, cache=cache)
    if r.estado == "omitida":
        return RevalorizarItem(
            **base, estado="sin_precio" if r.sin_precio else "omitida",
            motivo=r.motivo or "Todavía sin precio",
        )
    # Mismos montos: nada que cambiar, salvo sacarle la marca a una que la tenga.
    if r.estado == "igual" and not row.sin_valorizar:
        return None
    if aplicar:
        recotizar.aplicar(row, r)
    return RevalorizarItem(
        **base, estado="revalorizada", honorarios=r.honorarios, gastos=r.gastos,
        ayudante=r.ayudante, coseguro=r.coseguro, importe_despues=r.importe_total,
    )


async def revalorizar(db: AsyncSession, body: RevalorizarIn) -> RevalorizarOut:
    with recotizar.memo_por_corrida(db):
        return await _revalorizar(db, body)


async def _revalorizar(db: AsyncSession, body: RevalorizarIn) -> RevalorizarOut:
    filas = await pendientes(db, body.cod_obra, body.codigo)
    if body.ids is not None:
        pedidas = set(body.ids)
        filas = [f for f in filas if f.id_detalle_prestaciones in pedidas]
    items: list[RevalorizarItem] = []
    sin_cambios = 0
    cache = await recotizar.CacheCotizacion.para(db, body.cod_obra)
    for row in filas:
        try:
            item = await _revalorizar_fila(db, row, aplicar=not body.dry_run, cache=cache)
            if item is None:
                sin_cambios += 1
            else:
                items.append(item)
        except Exception as e:  # noqa: BLE001 — se informa por fila, no corta el resto
            items.append(RevalorizarItem(
                id=row.id_detalle_prestaciones, periodo=row.periodo, cod_med=str(row.cod_med),
                fecha_practica=row.fecha_practica, conceptos=row.sin_valorizar or "",
                importe_antes=row.importe_total or Decimal("0"),
                estado="error", motivo=str(getattr(e, "detail", e)),
            ))
    if body.dry_run:
        await db.rollback()
    else:
        await db.commit()
    return RevalorizarOut(
        dry_run=body.dry_run, cod_obra=body.cod_obra, codigo=body.codigo, total=len(items),
        revalorizadas=sum(1 for i in items if i.estado == "revalorizada"),
        sin_cambios=sin_cambios, items=items,
    )
