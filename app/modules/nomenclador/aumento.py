"""Aumento porcentual de una obra social: valores fijos y/o galenos.

- Valores FIJOS (NE/NN con precio embebido): se cierra cada valor y se abre uno
  nuevo con el % aplicado desde `vigencia_desde`.
- GALENOS elegidos (cirugía adulto, infantil, consulta, …): se rota el precio de
  cada nivel vigente. Eso actualiza solo todos los valores calculables que los
  usan (NN y NE por galeno), vía `regenerar_historial_por_galeno`.

Reglas que evitan romper datos (ver análisis del 2026-09-30):
- La vigencia tiene que ser POSTERIOR a la del valor/galeno vigente. Si no, se
  omite con motivo: aplicar dos veces con la misma fecha no acumula (+10% dos
  veces ≠ +21%) y una fecha vieja no cierra un valor antes de su inicio.
- "Por presupuesto" no tiene precio que aumentar: se omite.
- Cada ítem corre en su propio SAVEPOINT: uno que falla no tira abajo al resto.
- `dry_run=True` calcula exactamente lo mismo sin escribir (vista previa).
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.nomenclador_cmc import Galeno, HistorialPrecioCodigo, Valor, ValorComponente
from app.modules.nomenclador import service
from app.modules.nomenclador.schemas import (
    ActualizarPorcentajeIn,
    AumentoDetalleItem,
    AumentoPorcentualResult,
    RevertirActualizacionIn,
)

CENT = Decimal("0.01")
MOTIVO_AUMENTO = "valor_fijo_actualizado"


def _en_rango(codigo: str, desde: str, hasta: str) -> bool:
    """Numérico si los tres son números (evita que "1500" caiga en 100000–199999)."""
    if codigo.isdigit() and desde.isdigit() and hasta.isdigit():
        return int(desde) <= int(codigo) <= int(hasta)
    return desde <= codigo <= hasta


def _fmt(d: datetime.date) -> str:
    return d.strftime("%d/%m/%Y")


async def aplicar_aumento(db: AsyncSession, body: ActualizarPorcentajeIn) -> AumentoPorcentualResult:
    from app.modules.nomenclador.routes_galenos import _rotar_precio_galeno
    from app.modules.nomenclador.routes_valores import (
        _cerrar_valor,
        _clonar_valor,
        _componentes_activos,
    )

    factor = Decimal("1") + body.porcentaje / Decimal("100")
    fecha_corte = body.vigencia_desde - datetime.timedelta(days=1)
    detalle: list[AumentoDetalleItem] = []
    errores: list[dict] = []
    actualizados = omitidos = galenos_actualizados = 0

    # ── Valores fijos ────────────────────────────────────────────────────────
    if body.incluir_valores_fijos:
        stmt = select(Valor).where(
            Valor.obra_social_nro == body.obra_social_nro,
            Valor.origen == body.origen.value,
            Valor.estado == "activo",
        ).order_by(Valor.codigo, Valor.especialidad_id_colegio)
        if body.filtro_codigos:
            stmt = stmt.where(Valor.codigo.in_(body.filtro_codigos))
        valores = list((await db.execute(stmt)).scalars())
        if body.filtro_rango:
            desde = str(body.filtro_rango.get("desde", "")).strip()
            hasta = str(body.filtro_rango.get("hasta", "")).strip()
            valores = [v for v in valores if _en_rango(v.codigo, desde, hasta)]

        for v in valores:
            comps = await _componentes_activos(db, v.id)
            actual = sum((c.subtotal for c in comps), Decimal("0"))
            item = AumentoDetalleItem(
                tipo="valor", codigo=v.codigo, descripcion=v.descripcion,
                especialidad_id_colegio=v.especialidad_id_colegio, nivel=v.nivel,
                vigencia_actual=v.vigencia_desde, actual=actual,
            )
            motivo: Optional[str] = None
            if v.por_presupuesto:
                motivo = "Por presupuesto: no tiene precio que aumentar"
            elif not comps or service.modalidad_de(comps) == "galeno":
                motivo = (
                    "Se calcula por galeno: se actualiza con el galeno"
                    if body.galeno_codigos else "Se calcula por galeno: elegí sus galenos para aumentarlo"
                )
            elif body.vigencia_desde <= v.vigencia_desde:
                motivo = f"Ya tiene una vigencia igual o posterior ({_fmt(v.vigencia_desde)})"
            if motivo:
                item.motivo = motivo
                omitidos += 1
                detalle.append(item)
                continue

            nuevo_total = sum(
                ((c.valor_unitario or Decimal("0")) * factor).quantize(CENT) for c in comps
            )
            item.nuevo = nuevo_total
            item.estado = "actualiza"
            if body.dry_run:
                actualizados += 1
                detalle.append(item)
                continue

            def _ajustar(c: ValorComponente) -> dict:
                return {
                    "concepto": c.concepto,
                    "galeno_id": c.galeno_id,
                    "cantidad": c.cantidad,
                    "valor_unitario": (
                        (c.valor_unitario * factor).quantize(CENT)
                        if c.valor_unitario is not None else None
                    ),
                    "orden": c.orden,
                    "observacion": c.observacion,
                }

            try:
                async with db.begin_nested():
                    _cerrar_valor(v, fecha_corte)
                    await db.flush()
                    await _clonar_valor(
                        db, v, body.vigencia_desde, transform_componente=_ajustar,
                        motivo=MOTIVO_AUMENTO, fecha_corte=fecha_corte,
                    )
                actualizados += 1
            except Exception as e:  # el savepoint ya revirtió solo este valor
                item.estado = "error"
                item.motivo = str(e)
                errores.append({"codigo": v.codigo, "motivo": str(e)})
            detalle.append(item)

    # ── Galenos ──────────────────────────────────────────────────────────────
    if body.galeno_codigos:
        galenos = list((await db.execute(
            select(Galeno).where(
                Galeno.obra_social_nro == body.obra_social_nro,
                Galeno.codigo.in_(body.galeno_codigos),
                Galeno.vigencia_hasta.is_(None),
                Galeno.activo == True,  # noqa: E712
            ).order_by(Galeno.codigo, Galeno.nivel)
        )).scalars())
        for g in galenos:
            nuevo_vu = (g.valor_unitario * factor).quantize(CENT)
            item = AumentoDetalleItem(
                tipo="galeno", codigo=g.codigo, descripcion=g.nombre, nivel=g.nivel,
                vigencia_actual=g.vigencia_desde, actual=g.valor_unitario,
            )
            if body.vigencia_desde <= g.vigencia_desde:
                item.motivo = f"Ya tiene una vigencia igual o posterior ({_fmt(g.vigencia_desde)})"
                omitidos += 1
                detalle.append(item)
                continue
            item.nuevo = nuevo_vu
            item.estado = "actualiza"
            if not body.dry_run:
                try:
                    async with db.begin_nested():
                        await _rotar_precio_galeno(db, g, nuevo_vu, body.vigencia_desde)
                except Exception as e:
                    msg = getattr(e, "detail", None) or str(e)
                    item.estado = "error"
                    item.motivo = str(msg)
                    errores.append({"codigo": g.codigo, "nivel": g.nivel, "motivo": str(msg)})
                    detalle.append(item)
                    continue
            galenos_actualizados += 1
            detalle.append(item)

    if body.dry_run:
        await db.rollback()
    else:
        await db.commit()

    return AumentoPorcentualResult(
        actualizados=actualizados,
        galenos_actualizados=galenos_actualizados,
        omitidos=omitidos,
        errores=errores,
        detalle=detalle,
        dry_run=body.dry_run,
    )


async def revertir_aumento(db: AsyncSession, body: RevertirActualizacionIn) -> AumentoPorcentualResult:
    """Deshace un aumento de una fecha: SOLO lo que abrió un aumento/actualización de
    precio fijo con esa vigencia (no altas ni ediciones) y los galenos rotados ese
    día. Nunca deja un código sin precio: si no hay valor anterior, se omite."""
    from app.modules.nomenclador.routes_valores import (
        _cerrar_valor,
        _componentes_activos,
        _cond_variante_valor,
    )

    fecha = body.vigencia_revertir
    fecha_corte = fecha - datetime.timedelta(days=1)
    detalle: list[AumentoDetalleItem] = []
    errores: list[dict] = []
    actualizados = omitidos = galenos_actualizados = 0

    # ── Valores abiertos por un aumento en esa fecha ──────────────────────────
    ids_aumento = set((await db.execute(
        select(HistorialPrecioCodigo.valores_id).where(
            HistorialPrecioCodigo.obra_social_nro == body.obra_social_nro,
            HistorialPrecioCodigo.vigencia_desde == fecha,
            HistorialPrecioCodigo.motivo_cambio == MOTIVO_AUMENTO,
        )
    )).scalars())
    valores = list((await db.execute(
        select(Valor).where(
            Valor.obra_social_nro == body.obra_social_nro,
            Valor.vigencia_desde == fecha,
            Valor.estado == "activo",
            Valor.id.in_(ids_aumento or {-1}),
        ).order_by(Valor.codigo)
    )).scalars())

    for v in valores:
        comps = await _componentes_activos(db, v.id)
        item = AumentoDetalleItem(
            tipo="valor", codigo=v.codigo, descripcion=v.descripcion,
            especialidad_id_colegio=v.especialidad_id_colegio, nivel=v.nivel,
            vigencia_actual=v.vigencia_desde,
            actual=sum((c.subtotal for c in comps), Decimal("0")),
        )
        anterior = (await db.execute(
            select(Valor).where(
                Valor.obra_social_nro == body.obra_social_nro,
                Valor.nomenclador_id == v.nomenclador_id,
                _cond_variante_valor(v.origen, v.especialidad_id_colegio),
                Valor.estado == "cerrado",
                Valor.vigencia_desde < fecha,
            ).order_by(Valor.vigencia_desde.desc(), Valor.id.desc()).limit(1)
        )).scalars().first()
        if anterior is None:
            item.motivo = "No hay valor anterior: quedaría sin precio"
            omitidos += 1
            detalle.append(item)
            continue
        comps_ant = await _componentes_activos(db, anterior.id)
        item.nuevo = sum((c.subtotal for c in comps_ant), Decimal("0"))
        item.estado = "actualiza"
        if not body.dry_run:
            try:
                async with db.begin_nested():
                    # Se cierra el MISMO día que empezó (no el anterior): así no queda
                    # una fila con fecha de fin previa a su inicio.
                    _cerrar_valor(v, fecha)
                    anterior.estado = "activo"
                    anterior.vigencia_hasta = None
                    await db.flush()
                    await service.cerrar_historial_de_valor(v.id, fecha_corte, db)
                    await service.regenerar_historial_por_valores(
                        anterior.id, None, db, motivo="reversion", nueva_vigencia_desde=fecha,
                    )
            except Exception as e:
                item.estado = "error"
                item.motivo = str(e)
                errores.append({"codigo": v.codigo, "motivo": str(e)})
                detalle.append(item)
                continue
        actualizados += 1
        detalle.append(item)

    # ── Galenos rotados en esa fecha ──────────────────────────────────────────
    nuevos = list((await db.execute(
        select(Galeno).where(
            Galeno.obra_social_nro == body.obra_social_nro,
            Galeno.vigencia_desde == fecha,
            Galeno.vigencia_hasta.is_(None),
            Galeno.activo == True,  # noqa: E712
        ).order_by(Galeno.codigo, Galeno.nivel)
    )).scalars())
    for g in nuevos:
        previo = (await db.execute(
            select(Galeno).where(
                Galeno.obra_social_nro == g.obra_social_nro,
                Galeno.codigo == g.codigo,
                Galeno.nivel.is_(None) if g.nivel is None else Galeno.nivel == g.nivel,
                Galeno.vigencia_hasta == fecha_corte,
            ).order_by(Galeno.id.desc()).limit(1)
        )).scalars().first()
        item = AumentoDetalleItem(
            tipo="galeno", codigo=g.codigo, descripcion=g.nombre, nivel=g.nivel,
            vigencia_actual=g.vigencia_desde, actual=g.valor_unitario,
        )
        if previo is None:
            # Galeno creado ese día (no rotado): no hay a qué volver.
            continue
        item.nuevo = previo.valor_unitario
        item.estado = "actualiza"
        if not body.dry_run:
            try:
                async with db.begin_nested():
                    previo.vigencia_hasta = None
                    previo.activo = True
                    g.activo = False
                    g.vigencia_hasta = fecha
                    await db.flush()
                    await service.regenerar_historial_por_galeno(
                        galeno_id_anterior=g.id, nuevo_galeno_id=previo.id,
                        vigencia_desde=fecha, db=db,
                    )
            except Exception as e:
                item.estado = "error"
                item.motivo = str(e)
                errores.append({"codigo": g.codigo, "nivel": g.nivel, "motivo": str(e)})
                detalle.append(item)
                continue
        galenos_actualizados += 1
        detalle.append(item)

    if body.dry_run:
        await db.rollback()
    else:
        await db.commit()

    return AumentoPorcentualResult(
        actualizados=actualizados,
        galenos_actualizados=galenos_actualizados,
        omitidos=omitidos,
        errores=errores,
        detalle=detalle,
        dry_run=body.dry_run,
    )
