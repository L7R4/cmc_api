"""Replicar en las obras sociales de la misma familia (planes de una empresa).

Una "familia" son las OS unidas por `obras_sociales.obra_social_principal_id`
(ver `app/modules/catalogs/familia_padron.py`). Desde Por obra social y Galenos,
el operador puede pedir que el cambio que acaba de hacer en una OS se haga igual
en los otros planes de la familia.

Reglas:
- Se replica SOLO la operación hecha (alta, lápiz del código, lápiz de una
  variante; alta / precio / unidades de un galeno), nunca se "sincroniza" todo.
- Precio fijo: se copia igual. Calculable: se usa el galeno de la OS destino con
  el mismo código y nivel; si no lo tiene vigente, ese destino se omite.
- Si al editar un código el destino no lo tiene → se crea copiando cómo quedó
  en la OS de origen. Al actualizar el valor de un galeno que el destino no
  tiene → se crea con ese valor.
- La vigencia tiene que ser posterior a la vigente del destino (si no, omitido:
  evita fechas invertidas y pisar precios del mismo día).
- Cada destino corre en su SAVEPOINT: uno que falla no afecta a los demás.
- Se llama DESPUÉS de guardar la OS de origen: nada de acá deshace el origen.
"""
from __future__ import annotations

import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.catalogs import ObrasSociales
from app.db.models.nomenclador_cmc import Galeno, NomencladorCMC, Valor
from app.modules.catalogs.familia_padron import codigos_de_familia
from app.modules.nomenclador import service
from app.modules.nomenclador.schemas import (
    ObraSocialFamiliaItem,
    ReplicaResultadoItem,
    ReplicarFamiliaGalenoIn,
    ReplicarFamiliaOut,
    ReplicarFamiliaValorIn,
    ValorCerrarYCrearIn,
    ValorComponenteIn,
)


class _Omitir(Exception):
    """Motivo de negocio para no replicar en un destino (no es un error)."""


def _fmt(d: datetime.date) -> str:
    return d.strftime("%d/%m/%Y")


# ─── Familia ─────────────────────────────────────────────────────────────────

async def familia_de(db: AsyncSession, nro_os: int) -> list[ObraSocialFamiliaItem]:
    """Las OTRAS OS activas de la familia de `nro_os` (vacío si no tiene)."""
    nros = [n for n in await codigos_de_familia(db, nro_os) if n != nro_os]
    if not nros:
        return []
    filas = (await db.execute(
        select(ObrasSociales).where(
            ObrasSociales.NRO_OBRASOCIAL.in_(nros), ObrasSociales.MARCA != "N"
        ).order_by(ObrasSociales.OBRA_SOCIAL)
    )).scalars().all()
    return [
        ObraSocialFamiliaItem(
            nro_obra_social=f.NRO_OBRASOCIAL,
            nombre=(f.OBRA_SOCIAL or "").strip(),
            es_principal=f.obra_social_principal_id is None,
        )
        for f in filas
    ]


async def _destinos_validos(db: AsyncSession, origen: int, destinos: list[int]) -> dict[int, str]:
    familia = {f.nro_obra_social: f.nombre for f in await familia_de(db, origen)}
    fuera = [d for d in destinos if d not in familia]
    if fuera:
        raise HTTPException(
            422, f"Estas obras sociales no son de la familia de {origen}: {sorted(fuera)}"
        )
    return {d: familia[d] for d in dict.fromkeys(destinos)}


# ─── Valores ─────────────────────────────────────────────────────────────────

async def _componentes_para_os(
    db: AsyncSession, componentes: list[ValorComponenteIn], os_dest: int
) -> list[ValorComponenteIn]:
    """Componentes de la OS de origen → los de la OS destino: fijos iguales,
    calculables con el galeno vigente del destino del mismo código y nivel."""
    salida: list[ValorComponenteIn] = []
    for c in componentes:
        if c.galeno_id is None:
            salida.append(c)
            continue
        galeno = await db.get(Galeno, c.galeno_id)
        if galeno is None:
            raise _Omitir(f"El galeno {c.galeno_id} de origen no existe")
        dest = await service.buscar_galeno_vigente(db, os_dest, galeno.codigo, galeno.nivel)
        if dest is None:
            nivel = f" nivel {galeno.nivel}" if galeno.nivel is not None else ""
            raise _Omitir(f"No tiene vigente el galeno {galeno.nombre}{nivel}")
        salida.append(c.model_copy(update={"galeno_id": dest.id}))
    return salida


def _comp_in(c) -> ValorComponenteIn:
    return ValorComponenteIn(
        concepto=c.concepto, galeno_id=c.galeno_id, cantidad=c.cantidad,
        valor_unitario=c.valor_unitario, orden=c.orden, observacion=c.observacion,
    )


async def _activos(db: AsyncSession, os_nro: int, nom_id: int) -> list[Valor]:
    return list((await db.execute(
        select(Valor).where(
            Valor.obra_social_nro == os_nro,
            Valor.nomenclador_id == nom_id,
            Valor.estado == "activo",
        ).order_by(Valor.id)
    )).scalars())


async def _habilitar(db: AsyncSession, codigo: str, os_nro: int, esp: int) -> None:
    try:
        await service.validar_especialidad_habilitada(db, codigo, os_nro, esp)
    except ValueError as e:
        raise _Omitir(str(e))


async def _copiar_estado(db: AsyncSession, origen: int, dest: int, nom: NomencladorCMC) -> None:
    """El destino no tiene el código: se crea igual a como está en la OS de origen
    (variantes activas, especialidades habilitadas y "sin restricción")."""
    from app.modules.nomenclador.routes_valores import (
        _componentes_activos,
        _crear_valor_con_componentes,
    )

    filas = await _activos(db, origen, nom.id)
    if not filas:
        raise _Omitir("El código tampoco tiene valores activos en la obra social de origen")
    for esp in await service.especialidades_habilitadas_de(db, nom.codigo, origen):
        await _habilitar(db, nom.codigo, dest, esp)
    for v in filas:
        comps = [_comp_in(c) for c in await _componentes_activos(db, v.id)]
        await _crear_valor_con_componentes(
            db=db, obra_social_nro=dest, nomenclador_id=nom.id, origen=v.origen,
            vigencia_desde=v.vigencia_desde,
            componentes_in=await _componentes_para_os(db, comps, dest),
            descripcion=v.descripcion, nivel=v.nivel, complejidad=v.complejidad,
            especialidad_id_colegio=v.especialidad_id_colegio, observacion=v.observacion,
            por_presupuesto=v.por_presupuesto, cantidad_ayudantes=v.cantidad_ayudantes,
            categoria=v.categoria, requiere_autorizacion=v.requiere_autorizacion,
            coseguro=v.coseguro, sin_restriccion_especialidad=v.sin_restriccion_especialidad,
            motivo="replicacion",
        )


def _vigencia_posterior(filas: list[Valor], vigencia: datetime.date) -> None:
    ultima = max((v.vigencia_desde for v in filas), default=None)
    if ultima is not None and vigencia <= ultima:
        raise _Omitir(f"Ya tiene una vigencia igual o posterior ({_fmt(ultima)})")


async def _replicar_valor_en(
    db: AsyncSession, body: ReplicarFamiliaValorIn, nom: NomencladorCMC, dest: int
) -> tuple[str, Optional[str]]:
    from app.modules.nomenclador.nucleo import actualizar_nucleo
    from app.modules.nomenclador.routes_valores import (
        _actualizar_valor_core,
        _buscar_valor_activo,
        _crear_valor_con_componentes,
    )

    filas_dest = await _activos(db, dest, nom.id)

    if body.operacion == "alta":
        a = body.alta
        esps = a.especialidades_id_colegio or [None]
        comps = await _componentes_para_os(db, a.componentes, dest)
        if a.sin_restriccion_especialidad:
            await service.fijar_sin_restriccion_par(db, dest, nom.codigo, True)
        sin_restriccion = bool(a.sin_restriccion_especialidad) or await service.par_sin_restriccion(
            db, nom.codigo, dest
        )
        creadas = 0
        for esp in esps:
            if await _buscar_valor_activo(db, dest, nom.id, a.origen.value, esp) is not None:
                continue
            if a.origen.value == "NE" and esp is not None:
                await _habilitar(db, nom.codigo, dest, esp)
            await _crear_valor_con_componentes(
                db=db, obra_social_nro=dest, nomenclador_id=nom.id, origen=a.origen.value,
                vigencia_desde=a.vigencia_desde, componentes_in=comps,
                descripcion=a.descripcion, nivel=a.nivel, complejidad=a.complejidad,
                especialidad_id_colegio=esp, observacion=a.observacion,
                por_presupuesto=a.por_presupuesto, cantidad_ayudantes=a.cantidad_ayudantes,
                categoria=a.categoria, requiere_autorizacion=a.requiere_autorizacion,
                coseguro=a.coseguro, sin_restriccion_especialidad=sin_restriccion,
                motivo="replicacion",
            )
            creadas += 1
        if creadas == 0:
            raise _Omitir("Ya existe")
        return "replicado", None

    if not filas_dest:
        await _copiar_estado(db, body.origen_obra_social_nro, dest, nom)
        return "creado", "Se creó el código"

    if body.operacion == "nucleo":
        nuc = body.nucleo
        if nuc.ecuacion is not None:
            mismo_origen = [v for v in filas_dest if v.origen == nuc.origen]
            _vigencia_posterior(mismo_origen, nuc.ecuacion.vigencia_desde)
            ecu = nuc.ecuacion.model_copy(update={
                "componentes": await _componentes_para_os(db, nuc.ecuacion.componentes, dest),
            })
            nuc = nuc.model_copy(update={"ecuacion": ecu})
        await actualizar_nucleo(db, dest, nom.id, nuc)
        return "replicado", None

    # variante
    var = body.variante
    ecu = var.ecuacion.model_copy(update={
        "componentes": await _componentes_para_os(db, var.ecuacion.componentes, dest),
        "aplicar_a_variantes": False,
    })
    actual = await _buscar_valor_activo(db, dest, nom.id, var.origen.value, var.especialidad_id_colegio)
    if actual is not None:
        _vigencia_posterior([actual], ecu.vigencia_desde)
        await _actualizar_valor_core(db, actual.id, ecu)
        return "replicado", None

    # El código existe pero no esa variante: se crea con los datos del código en el destino.
    base = filas_dest[0]
    if var.origen.value == "NE" and var.especialidad_id_colegio is not None:
        await _habilitar(db, nom.codigo, dest, var.especialidad_id_colegio)
    await _crear_valor_con_componentes(
        db=db, obra_social_nro=dest, nomenclador_id=nom.id, origen=var.origen.value,
        vigencia_desde=ecu.vigencia_desde, componentes_in=ecu.componentes,
        descripcion=ecu.descripcion or base.descripcion, nivel=base.nivel,
        complejidad=base.complejidad, especialidad_id_colegio=var.especialidad_id_colegio,
        observacion=base.observacion, por_presupuesto=ecu.por_presupuesto,
        cantidad_ayudantes=base.cantidad_ayudantes, categoria=base.categoria,
        requiere_autorizacion=base.requiere_autorizacion,
        coseguro=ecu.coseguro if ecu.coseguro is not None else base.coseguro,
        sin_restriccion_especialidad=base.sin_restriccion_especialidad,
        motivo="replicacion",
    )
    return "creado", "Se creó la variante"


async def replicar_valores(db: AsyncSession, body: ReplicarFamiliaValorIn) -> ReplicarFamiliaOut:
    nom = await db.get(NomencladorCMC, body.nomenclador_id)
    if nom is None:
        raise HTTPException(404, "Código de nomenclador no encontrado")
    destinos = await _destinos_validos(db, body.origen_obra_social_nro, body.destinos)
    resultados = [await _en_savepoint(db, nro, nombre, _replicar_valor_en(db, body, nom, nro))
                  for nro, nombre in destinos.items()]
    await db.commit()
    return ReplicarFamiliaOut(resultados=resultados)


# ─── Galenos ─────────────────────────────────────────────────────────────────

async def _replicar_galeno_en(
    db: AsyncSession, body: ReplicarFamiliaGalenoIn, dest: int
) -> tuple[str, Optional[str]]:
    from app.modules.nomenclador.routes_galenos import _rotar_precio_galeno, _validar_mezcla_niveles

    if body.operacion == "alta":
        creados = 0
        for n in body.niveles:
            if await service.buscar_galeno_vigente(db, dest, body.codigo, n.nivel) is not None:
                continue
            await _validar_mezcla_niveles(db, dest, body.codigo, n.nivel)
            db.add(Galeno(
                obra_social_nro=dest, codigo=body.codigo, nombre=body.nombre, nivel=n.nivel,
                vigencia_desde=body.vigencia_desde, vigencia_hasta=None,
                valor_unitario=n.valor_unitario, unidades_honorarios=n.unidades_honorarios,
                unidades_ayudante=n.unidades_ayudante, unidades_gastos=n.unidades_gastos,
            ))
            creados += 1
        await db.flush()
        if creados == 0:
            raise _Omitir("Ya existe")
        return "replicado", None

    g = await service.buscar_galeno_vigente(db, dest, body.codigo, body.nivel)

    if body.operacion == "precio":
        if g is None:
            origen = await service.buscar_galeno_vigente(
                db, body.origen_obra_social_nro, body.codigo, body.nivel
            )
            if origen is None:
                raise _Omitir("El galeno tampoco está vigente en la obra social de origen")
            await _validar_mezcla_niveles(db, dest, body.codigo, body.nivel)
            db.add(Galeno(
                obra_social_nro=dest, codigo=body.codigo, nombre=origen.nombre, nivel=body.nivel,
                vigencia_desde=body.vigencia_desde, vigencia_hasta=None,
                valor_unitario=body.nuevo_valor_unitario,
                unidades_honorarios=origen.unidades_honorarios,
                unidades_ayudante=origen.unidades_ayudante,
                unidades_gastos=origen.unidades_gastos,
            ))
            await db.flush()
            return "creado", "Se creó el galeno"
        if body.vigencia_desde <= g.vigencia_desde:
            raise _Omitir(f"Ya tiene una vigencia igual o posterior ({_fmt(g.vigencia_desde)})")
        await _rotar_precio_galeno(db, g, body.nuevo_valor_unitario, body.vigencia_desde)
        return "replicado", None

    # unidades
    if g is None:
        raise _Omitir("No existe el galeno")
    campos = {"unidades_honorarios": "Honorarios", "unidades_ayudante": "Ayudante",
              "unidades_gastos": "Gastos"}
    provistos = body.model_fields_set & campos.keys()
    if not provistos:
        raise _Omitir("No hay unidades para aplicar")
    conceptos = []
    for f in provistos:
        valor = getattr(body, f)
        setattr(g, f, valor)
        if valor is not None:
            conceptos.append(campos[f])
    await db.flush()
    await service.propagar_unidades_galeno(g, conceptos, body.vigencia_desde, db)
    return "replicado", None


async def replicar_galenos(db: AsyncSession, body: ReplicarFamiliaGalenoIn) -> ReplicarFamiliaOut:
    destinos = await _destinos_validos(db, body.origen_obra_social_nro, body.destinos)
    resultados = [await _en_savepoint(db, nro, nombre, _replicar_galeno_en(db, body, nro))
                  for nro, nombre in destinos.items()]
    await db.commit()
    return ReplicarFamiliaOut(resultados=resultados)


# ─── Común ───────────────────────────────────────────────────────────────────

async def _en_savepoint(db: AsyncSession, nro: int, nombre: str, trabajo) -> ReplicaResultadoItem:
    try:
        async with db.begin_nested():
            estado, motivo = await trabajo
        return ReplicaResultadoItem(obra_social_nro=nro, nombre=nombre, estado=estado, motivo=motivo)
    except _Omitir as e:
        return ReplicaResultadoItem(obra_social_nro=nro, nombre=nombre, estado="omitido", motivo=str(e))
    except HTTPException as e:
        return ReplicaResultadoItem(obra_social_nro=nro, nombre=nombre, estado="omitido", motivo=str(e.detail))
    except Exception as e:  # noqa: BLE001 — se reporta por OS, no corta el resto
        return ReplicaResultadoItem(obra_social_nro=nro, nombre=nombre, estado="error", motivo=str(e))
