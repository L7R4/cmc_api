"""Revalorizar prestaciones cargadas SIN PRECIO.

Cuando un código está dado de alta en una O.S. pero todavía no tiene precio, y
`CARGA_SIN_PRECIO` lo permite, la prestación se carga en $0 con
`detalle_facturacion.sin_valorizar` = los conceptos que hay que cotizar ("H", "G",
"A"). Al cargar el precio (etapa 4 del nomenclador), esto recalcula las que
siguen ABIERTAS (estado 'A'): mismo cálculo que la carga (`_insertar_prestaciones`)
— precio del médico a la fecha de la práctica, concepto por concepto, porcentaje,
gastos en 0 bajo sanatorio y coseguro sugerido.

Las de períodos cerrados no se tocan. Con `dry_run` sólo informa antes/después.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.cmc_facturacion import DetalleFacturacionCMC
from app.modules.facturacion import service
from app.modules.nomenclador import service_vias


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
    estado: Literal["revalorizada", "sin_precio", "error"]
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
    items: List[RevalorizarItem] = Field(default_factory=list)


async def pendientes(db: AsyncSession, cod_obra: str, codigo: str) -> list[DetalleFacturacionCMC]:
    D = DetalleFacturacionCMC
    return list((await db.execute(
        select(D).where(
            D.cod_obr == cod_obra, D.cod_nom == codigo, D.estado == "A",
            D.sin_valorizar.is_not(None),
        ).order_by(D.periodo, D.id_detalle_prestaciones)
    )).scalars())


async def contar_pendientes(db: AsyncSession, cod_obra: str, codigo: str) -> int:
    return len(await pendientes(db, cod_obra, codigo))


async def _revalorizar_fila(db: AsyncSession, row: DetalleFacturacionCMC, aplicar: bool) -> RevalorizarItem:
    conceptos = row.sin_valorizar or "HG"
    base = dict(
        id=row.id_detalle_prestaciones, periodo=row.periodo, cod_med=row.cod_med,
        fecha_practica=row.fecha_practica, conceptos=conceptos,
        importe_antes=row.importe_total or Decimal("0"),
    )
    prestador = await service.resolver_prestador(
        db, row.cod_med, None, row.cod_clinica, validar_clinica=False,
    )
    precio = await service.resolver_precio(
        db, row.cod_obr, prestador.medico, row.cod_nom,
        service.fecha_para_precio(row.fecha_practica),
        via=row.via or service_vias.VIA_TRADICIONAL,
    )
    if not precio.admitido or precio.sin_precio:
        return RevalorizarItem(**base, estado="sin_precio", motivo=precio.motivo or "Todavía sin precio")
    if precio.por_presupuesto:
        return RevalorizarItem(
            **base, estado="error",
            motivo="El código es por presupuesto: el monto se carga a mano en la prestación",
        )

    hb = precio.honorarios if "H" in conceptos else Decimal("0")
    gb = precio.gastos if "G" in conceptos else Decimal("0")
    ab = precio.ayudante if "A" in conceptos else Decimal("0")
    if await service._gasto_forzado_a_cero(db, row.cod_nom, prestador.es_sanatorio, "A", row.cod_obr):
        gb = Decimal("0")
    h, g, a = service._aplicar_porcentaje(hb, gb, ab, row.porc or 100)

    es_pediatra = (row.tpo_funcion or "").upper() == service.TPO_FUNCION_PEDIATRA
    # El coseguro es del acto: sólo lo lleva la fila del médico (no ayudante ni pediatra).
    coseguro = Decimal("0") if (es_pediatra or "A" in conceptos) else (row.coseguro or precio.coseguro)
    coseguro = min(coseguro, h + g + a)
    total = service.calcular_importe_total(h, g, a, row.cantidad or 1, row.sesion or 1, coseguro=coseguro)

    if aplicar:
        row.honorarios, row.gastos, row.ayudante = h, g, a
        row.coseguro = coseguro
        row.importe_total = total
        row.calculo_snapshot = precio.snapshot
        row.tpo_funcion = service.tpo_funcion_de(h, g, a, service.ROL_PEDIATRA if es_pediatra else None)
        row.sin_valorizar = None
    return RevalorizarItem(
        **base, estado="revalorizada", honorarios=h, gastos=g, ayudante=a,
        coseguro=coseguro, importe_despues=total,
    )


async def revalorizar(db: AsyncSession, body: RevalorizarIn) -> RevalorizarOut:
    filas = await pendientes(db, body.cod_obra, body.codigo)
    if body.ids is not None:
        pedidas = set(body.ids)
        filas = [f for f in filas if f.id_detalle_prestaciones in pedidas]
    items: list[RevalorizarItem] = []
    for row in filas:
        try:
            items.append(await _revalorizar_fila(db, row, aplicar=not body.dry_run))
        except Exception as e:  # noqa: BLE001 — se informa por fila, no corta el resto
            items.append(RevalorizarItem(
                id=row.id_detalle_prestaciones, periodo=row.periodo, cod_med=row.cod_med,
                fecha_practica=row.fecha_practica, conceptos=row.sin_valorizar or "",
                estado="error", motivo=str(getattr(e, "detail", e)),
            ))
    if body.dry_run:
        await db.rollback()
    else:
        await db.commit()
    return RevalorizarOut(
        dry_run=body.dry_run, cod_obra=body.cod_obra, codigo=body.codigo, total=len(items),
        revalorizadas=sum(1 for i in items if i.estado == "revalorizada"), items=items,
    )
