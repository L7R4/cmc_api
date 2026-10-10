"""Replicar la base de la obra social CABECERA en una derivada recién creada.

Una obra social es o bien una cabecera (única) o bien una derivada de otra
(`obras_sociales.obra_social_principal_id`). Al crear una derivada se puede pedir que arranque
con lo mismo que tiene su cabecera, en tres bloques que se pueden elegir por separado:

- **galenos**: los galenos VIGENTES de la cabecera (precio, unidades y niveles).
- **nomencladores**: los códigos dados de alta en la cabecera con quién factura cada uno (sin
  precio) y los nomencladores nivelados que la cabecera tiene aplicados (alta + precio con el
  galeno del nivel de la derivada: necesitan sus galenos, por eso sin «galenos» se omiten).
- **valores**: los precios activos de la cabecera.

Reglas:
- Se copia SOLO lo vigente hoy, con su misma vigencia desde; nunca el historial.
- Lo que la derivada ya tiene no se pisa; se informa como «ya existía».
- Un precio que depende de un galeno que la derivada no tiene se omite y se informa cuál.
- Cada bloque corre en su SAVEPOINT: si uno falla, los demás siguen y el error se informa.
- Se llama DESPUÉS de crear la obra social y NO hace commit: la obra social ya existe aunque
  un bloque falle.

Orden (lo arma la ruta de alta): galenos → códigos → nivelados → valores → sembrado NN. Todo va
antes del sembrado: con los galenos reales los NN nacen con precio y lo que se copia (códigos, NN
propios de la cabecera) no lo pisa el sembrado, que solo completa lo que falta.
"""
from __future__ import annotations

import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.nomenclador_cmc import (
    CodigoObraSocial,
    Galeno,
    NomencladorCMC,
    NomencladorNivelado,
    NomencladorNiveladoCodigo,
    Valor,
    ValorComponente,
    ValorEspecialidad,
)
from app.modules.catalogs.schemas import ReplicacionPasoOut
from app.modules.nomenclador import nivelados, service

LOTE = 300
MAX_DETALLE = 10


def _paso(
    paso: str, creados: int, omitidos: int, detalle: list[str], ya_existian: int = 0,
) -> ReplicacionPasoOut:
    return ReplicacionPasoOut(
        paso=paso,  # type: ignore[arg-type]
        estado="parcial" if omitidos and creados else ("omitido" if omitidos and not creados else "ok"),
        creados=creados, ya_existian=ya_existian, omitidos=omitidos, detalle=detalle[:MAX_DETALLE],
    )


def _error(paso: str, e: Exception) -> ReplicacionPasoOut:
    motivo = e.detail if isinstance(e, HTTPException) else str(e)
    if isinstance(motivo, dict):
        motivo = motivo.get("mensaje") or str(motivo)
    return ReplicacionPasoOut(paso=paso, estado="error", detalle=[str(motivo)])  # type: ignore[arg-type]


def _lotes(items: list, n: int = LOTE):
    for i in range(0, len(items), n):
        yield items[i:i + n]


# ─── Galenos ─────────────────────────────────────────────────────────────────

async def copiar_galenos(db: AsyncSession, cab: int, dest: int) -> ReplicacionPasoOut:
    """Los galenos vigentes de la cabecera, con su vigencia desde."""
    try:
        async with db.begin_nested():
            filas = list((await db.execute(
                select(Galeno).where(Galeno.obra_social_nro == cab, Galeno.activo == True)  # noqa: E712
            )).scalars())
            ya = {
                (g.codigo, g.nivel, g.vigencia_desde) for g in (await db.execute(
                    select(Galeno).where(Galeno.obra_social_nro == dest)
                )).scalars()
            }
            nuevos = [g for g in filas if (g.codigo, g.nivel, g.vigencia_desde) not in ya]
            for g in nuevos:
                db.add(Galeno(
                    obra_social_nro=dest, codigo=g.codigo, nombre=g.nombre, nivel=g.nivel,
                    vigencia_desde=g.vigencia_desde, vigencia_hasta=g.vigencia_hasta,
                    valor_unitario=g.valor_unitario,
                    unidades_honorarios=g.unidades_honorarios,
                    unidades_ayudante=g.unidades_ayudante, unidades_gastos=g.unidades_gastos,
                    activo=True, visible=g.visible, observacion=g.observacion,
                ))
            await db.flush()
        return _paso("galenos", len(nuevos), 0, [], ya_existian=len(filas) - len(nuevos))
    except Exception as e:  # noqa: BLE001 — se informa, no corta el alta
        return _error("galenos", e)


# ─── Códigos dados de alta ───────────────────────────────────────────────────

async def _copiar_habilitaciones(db: AsyncSession, cab: int, dest: int, codigos: set[str]) -> int:
    """Quién factura: las especialidades habilitadas en la cabecera para esos códigos y que la
    derivada todavía no tiene. Devuelve cuántas filas agregó."""
    if not codigos:
        return 0
    agregadas = 0
    for lote in _lotes(sorted(codigos)):
        ya = set((await db.execute(
            select(ValorEspecialidad.codigo, ValorEspecialidad.especialidad_id_colegio).where(
                ValorEspecialidad.obra_social_nro == dest, ValorEspecialidad.codigo.in_(lote),
            )
        )).all())
        nuevas = [
            {"obra_social_nro": dest, "codigo": cod, "especialidad_id_colegio": esp}
            for cod, esp in (await db.execute(
                select(ValorEspecialidad.codigo, ValorEspecialidad.especialidad_id_colegio).where(
                    ValorEspecialidad.obra_social_nro == cab, ValorEspecialidad.codigo.in_(lote),
                )
            )).all()
            if (cod, esp) not in ya
        ]
        if nuevas:
            await db.execute(insert(ValorEspecialidad), nuevas)
            agregadas += len(nuevas)
    return agregadas


async def copiar_codigos(
    db: AsyncSession, cab: int, dest: int, usuario: Optional[str],
) -> ReplicacionPasoOut:
    """Los códigos dados de alta en la cabecera (sin precio) con quién factura cada uno. Los que
    la derivada ya tiene (los NN que sembró el alta) no se tocan."""
    try:
        async with db.begin_nested():
            pares = list((await db.execute(
                select(CodigoObraSocial)
                .join(NomencladorCMC, NomencladorCMC.id == CodigoObraSocial.nomenclador_id)
                .where(CodigoObraSocial.obra_social_nro == cab, NomencladorCMC.activo == True)  # noqa: E712
            )).scalars())
            ya = set((await db.execute(
                select(CodigoObraSocial.nomenclador_id).where(CodigoObraSocial.obra_social_nro == dest)
            )).scalars())
            nuevos = [p for p in pares if p.nomenclador_id not in ya]
            for lote in _lotes(nuevos):
                await db.execute(insert(CodigoObraSocial), [
                    {
                        "obra_social_nro": dest, "nomenclador_id": p.nomenclador_id, "codigo": p.codigo,
                        "descripcion": p.descripcion, "categoria": p.categoria,
                        "complejidad": p.complejidad, "requiere_autorizacion": p.requiere_autorizacion,
                        "cantidad_ayudantes": p.cantidad_ayudantes,
                        "sin_restriccion_especialidad": p.sin_restriccion_especialidad,
                        "observacion": p.observacion, "estado": p.estado, "creado_por": usuario,
                    }
                    for p in lote
                ])
            await _copiar_habilitaciones(db, cab, dest, {p.codigo for p in nuevos})
        return _paso("codigos", len(nuevos), 0, [], ya_existian=len(pares) - len(nuevos))
    except Exception as e:  # noqa: BLE001
        return _error("codigos", e)


# ─── Nomencladores nivelados ─────────────────────────────────────────────────

async def _nivelados_aplicados(db: AsyncSession, cab: int) -> list[tuple[NomencladorNivelado, datetime.date]]:
    """Los nomencladores nivelados que la cabecera tiene aplicados, con la vigencia a usar.

    Aplicado = la cabecera tiene el galeno del nomenclador en todos sus niveles y TODOS los
    códigos del nomenclador con precio NE activo. (Cirugía de 7 y de 10 niveles comparten
    códigos: la cantidad de niveles del galeno es la que distingue cuál tiene aplicado.)
    La vigencia es la más reciente de esos precios NE en la cabecera."""
    out = []
    for n in (await db.execute(
        select(NomencladorNivelado).where(NomencladorNivelado.activo == True)  # noqa: E712
    )).scalars():
        nom_ids = list((await db.execute(
            select(NomencladorNiveladoCodigo.nomenclador_id).where(
                NomencladorNiveladoCodigo.nomenclador_nivelado_id == n.id
            )
        )).scalars())
        g_codigo, _ = await nivelados._galeno_de(db, n.galeno_grupo)
        if not nom_ids or g_codigo is None:
            continue
        niveles = (await db.execute(
            select(func.count(func.distinct(Galeno.nivel))).where(
                Galeno.obra_social_nro == cab, Galeno.codigo == g_codigo,
                Galeno.activo == True, Galeno.nivel.is_not(None),  # noqa: E712
            )
        )).scalar_one()
        if niveles != n.niveles:
            continue
        vigencias: dict[int, datetime.date] = {}
        for nom_id, vig in (await db.execute(
            select(Valor.nomenclador_id, func.max(Valor.vigencia_desde)).where(
                Valor.obra_social_nro == cab, Valor.nomenclador_id.in_(nom_ids),
                Valor.origen == "NE", Valor.estado == "activo",
            ).group_by(Valor.nomenclador_id)
        )).all():
            vigencias[nom_id] = vig
        if len(vigencias) < len(set(nom_ids)):
            continue
        out.append((n, max(vigencias.values())))
    return out


async def copiar_nivelados(
    db: AsyncSession, cab: int, dest: int, usuario: Optional[str],
) -> ReplicacionPasoOut:
    """Aplica a la derivada los nomencladores nivelados que tiene aplicados la cabecera."""
    creados = omitidos = 0
    detalle: list[str] = []
    try:
        aplicados = await _nivelados_aplicados(db, cab)
    except Exception as e:  # noqa: BLE001
        return _error("nivelados", e)
    for n, vigencia in aplicados:
        try:
            async with db.begin_nested():
                r = await nivelados.aplicar(db, n.slug, dest, vigencia, dry_run=False, usuario=usuario)
            creados += 1
            if r.resumen.omitido or r.resumen.suspendido or r.resumen.sin_quien_factura:
                detalle.append(
                    f"{n.nombre}: {r.resumen.omitido + r.resumen.suspendido} código(s) omitido(s), "
                    f"{r.resumen.sin_quien_factura} sin quién factura"
                )
        except HTTPException as e:
            omitidos += 1
            motivo = e.detail.get("mensaje") if isinstance(e.detail, dict) else e.detail
            detalle.append(f"{n.nombre}: {motivo}")
        except Exception as e:  # noqa: BLE001
            omitidos += 1
            detalle.append(f"{n.nombre}: {e}")
    if not aplicados:
        return ReplicacionPasoOut(paso="nivelados", estado="ok", detalle=["La cabecera no tiene nomencladores nivelados aplicados."])
    return _paso("nivelados", creados, omitidos, detalle)


# ─── Valores ─────────────────────────────────────────────────────────────────

async def copiar_valores(db: AsyncSession, cab: int, dest: int) -> ReplicacionPasoOut:
    """Los precios activos de la cabecera. Un precio calculable usa el galeno de la derivada con
    el mismo código y nivel; si no lo tiene, se omite."""
    try:
        async with db.begin_nested():
            # Galeno de la cabecera (id) → (código, nivel) → galeno vigente de la derivada.
            cab_galeno = {
                g.id: (g.codigo, g.nivel)
                for g in (await db.execute(select(Galeno).where(Galeno.obra_social_nro == cab))).scalars()
            }
            dest_galeno = {
                (g.codigo, g.nivel): g.id
                for g in (await db.execute(
                    select(Galeno).where(Galeno.obra_social_nro == dest, Galeno.activo == True)  # noqa: E712
                )).scalars()
            }
            ya = set((await db.execute(
                select(Valor.nomenclador_id, Valor.origen, Valor.especialidad_id_colegio).where(
                    Valor.obra_social_nro == dest, Valor.estado == "activo",
                )
            )).all())
            valores = list((await db.execute(
                select(Valor).where(Valor.obra_social_nro == cab, Valor.estado == "activo")
                .order_by(Valor.nomenclador_id, Valor.id)
            )).scalars())

            existentes = 0
            sin_galeno: dict[str, int] = {}
            pendientes = []
            for v in valores:
                if (v.nomenclador_id, v.origen, v.especialidad_id_colegio) in ya:
                    existentes += 1
                    continue
                pendientes.append(v)

            creados = 0
            codigos_creados: set[str] = set()
            for lote in _lotes(pendientes):
                comps_por_valor: dict[int, list[ValorComponente]] = {}
                for c in (await db.execute(
                    select(ValorComponente).where(
                        ValorComponente.valor_id.in_([v.id for v in lote]),
                        ValorComponente.activo == True,  # noqa: E712
                    ).order_by(ValorComponente.valor_id, ValorComponente.orden)
                )).scalars():
                    comps_por_valor.setdefault(c.valor_id, []).append(c)

                filas = []
                for v in lote:
                    comps, falta = [], None
                    for c in comps_por_valor.get(v.id, []):
                        gid = c.galeno_id
                        if gid is not None:
                            clave = cab_galeno.get(gid)
                            gid = dest_galeno.get(clave) if clave else None
                            if gid is None:
                                falta = clave[0] if clave else f"id {c.galeno_id}"
                                break
                        comps.append({
                            "concepto": c.concepto, "galeno_id": gid, "cantidad": c.cantidad,
                            "valor_unitario": c.valor_unitario, "orden": c.orden,
                            "observacion": c.observacion,
                        })
                    if falta is not None:
                        sin_galeno[falta] = sin_galeno.get(falta, 0) + 1
                        continue
                    filas.append(({
                        "obra_social_nro": dest, "nomenclador_id": v.nomenclador_id, "origen": v.origen,
                        "codigo": v.codigo, "descripcion": v.descripcion, "nivel": v.nivel,
                        "complejidad": v.complejidad, "categoria": v.categoria,
                        "requiere_autorizacion": v.requiere_autorizacion,
                        "especialidad_id_colegio": v.especialidad_id_colegio,
                        "sin_restriccion_especialidad": v.sin_restriccion_especialidad,
                        "por_presupuesto": v.por_presupuesto, "cantidad_ayudantes": v.cantidad_ayudantes,
                        "coseguro": v.coseguro, "vigencia_desde": v.vigencia_desde,
                        "observacion": v.observacion,
                    }, comps))
                    codigos_creados.add(v.codigo)
                if filas:
                    await service.persistir_valores_en_bloque(db, filas, motivo="replicacion")
                    creados += len(filas)

            # Las variantes NE por especialidad necesitan esa especialidad habilitada.
            await _copiar_habilitaciones(db, cab, dest, codigos_creados)

        omitidos = sum(sin_galeno.values())
        detalle = [
            f"{n} precio(s) omitido(s): la obra social nueva no tiene el galeno «{galeno}»."
            for galeno, n in sorted(sin_galeno.items(), key=lambda kv: -kv[1])
        ]
        return _paso("valores", creados, omitidos, detalle, ya_existian=existentes)
    except Exception as e:  # noqa: BLE001
        return _error("valores", e)
