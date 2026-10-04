"""Nomencladores nivelados: Cirugía adulto 7 y 10 niveles, Cirugía infantil 7,
FASGO 13, Urología 7…

Casi todas las obras sociales comparten el mismo nomenclador: un código tiene el
mismo nivel (o las mismas N unidades fijas) en todas; lo que cambia es el precio del
galeno por nivel, que pacta cada una. El nomenclador va ligado a un grupo de
`nm_galenos_plantilla` (de ahí sale el galeno nivelado con el que se cotiza).

Aplicarlo a una obra social (`aplicar`) da de alta cada código con su plantilla de
quién factura y crea un precio NE por especialidad:
  - Honorarios: el galeno del nivel del código, con las unidades del galeno.
    Los de "N unidades": el galeno de nivel 1 con cantidad N.
  - Ayudante: el mismo galeno, con sus unidades de ayudante (los de unidades, 0).
  - Gastos: `gasto_quirurgico` de la O.S. con las unidades del código, si lo tiene.
Lo que ya tiene precio en la O.S. se saltea. Ver `seed_nomencladores_nivelados.py`
para la carga inicial.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.catalogs import ObrasSociales
from app.db.models.nomenclador_cmc import (
    CodigoObraSocial,
    Galeno,
    GalenoPlantilla,
    NomencladorCMC,
    NomencladorNivelado,
    NomencladorNiveladoCodigo,
    NomencladorPlantillaEspecialidad,
    Valor,
    ValorEspecialidad,
)
from app.modules.nomenclador import alta_os, service
from app.modules.nomenclador.schemas import (
    AltaCodigoItem,
    AltaCodigosIn,
    AplicarNiveladoFila,
    AplicarNiveladoOut,
    AplicarNiveladoResumen,
    NiveladoCodigoAltaIn,
    NiveladoCodigoIn,
    NiveladoCodigoOut,
    NiveladoCodigosOut,
    NomencladorNiveladoOut,
)

GASTO_QUIRURGICO = "gasto_quirurgico"


# ─── Lectura ─────────────────────────────────────────────────────────────────

async def _get(db: AsyncSession, slug: str) -> NomencladorNivelado:
    nom = (await db.execute(
        select(NomencladorNivelado).where(NomencladorNivelado.slug == slug)
    )).scalar_one_or_none()
    if nom is None:
        raise HTTPException(404, "Nomenclador no encontrado")
    return nom


async def _galeno_de(db: AsyncSession, grupo: str) -> tuple[Optional[str], Optional[str]]:
    """(slug, nombre) del galeno de la plantilla."""
    fila = (await db.execute(
        select(GalenoPlantilla.codigo, GalenoPlantilla.nombre)
        .where(GalenoPlantilla.grupo == grupo).limit(1)
    )).first()
    return (fila.codigo, fila.nombre) if fila else (None, None)


async def listar(db: AsyncSession) -> list[NomencladorNiveladoOut]:
    noms = list((await db.execute(
        select(NomencladorNivelado).where(NomencladorNivelado.activo == True)
        .order_by(NomencladorNivelado.nombre)
    )).scalars())
    out = []
    for n in noms:
        filas = (await db.execute(
            select(NomencladorNiveladoCodigo.nivel, func.count())
            .where(NomencladorNiveladoCodigo.nomenclador_nivelado_id == n.id)
            .group_by(NomencladorNiveladoCodigo.nivel)
        )).all()
        por_nivel = {nivel: c for nivel, c in filas if nivel is not None}
        con_unidades = sum(c for nivel, c in filas if nivel is None)
        g_codigo, g_nombre = await _galeno_de(db, n.galeno_grupo)
        out.append(NomencladorNiveladoOut(
            id=n.id, slug=n.slug, nombre=n.nombre, galeno_grupo=n.galeno_grupo,
            galeno_codigo=g_codigo, galeno_nombre=g_nombre, niveles=n.niveles,
            total_codigos=sum(por_nivel.values()) + con_unidades,
            por_nivel=por_nivel, con_unidades=con_unidades,
        ))
    return out


async def listar_codigos(
    db: AsyncSession, slug: str, *, nivel: Optional[int], unidades: bool,
    q: Optional[str], page: int, size: int,
) -> NiveladoCodigosOut:
    n = await _get(db, slug)
    stmt = (
        select(NomencladorNiveladoCodigo, NomencladorCMC)
        .join(NomencladorCMC, NomencladorCMC.id == NomencladorNiveladoCodigo.nomenclador_id)
        .where(NomencladorNiveladoCodigo.nomenclador_nivelado_id == n.id)
    )
    if nivel is not None:
        stmt = stmt.where(NomencladorNiveladoCodigo.nivel == nivel)
    if unidades:
        stmt = stmt.where(NomencladorNiveladoCodigo.unidades.is_not(None))
    if q and q.strip():
        t = q.strip()
        digitos = "".join(ch for ch in t if ch.isdigit())
        conds = [NomencladorCMC.descripcion.contains(t)]
        if digitos:
            conds.append(NomencladorCMC.codigo.contains(digitos))
        stmt = stmt.where(or_(*conds))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    filas = (await db.execute(
        stmt.order_by(NomencladorCMC.codigo).offset((page - 1) * size).limit(size)
    )).all()
    return NiveladoCodigosOut(
        items=[_codigo_out(c, nom) for c, nom in filas], total=total, page=page, size=size,
    )


def _codigo_out(c: NomencladorNiveladoCodigo, nom: NomencladorCMC) -> NiveladoCodigoOut:
    return NiveladoCodigoOut(
        nomenclador_id=nom.id, codigo=nom.codigo, descripcion=nom.descripcion,
        activo=bool(nom.activo), nivel=c.nivel, unidades=c.unidades, observacion=c.observacion,
    )


# ─── Edición ─────────────────────────────────────────────────────────────────

def _validar_nivel(n: NomencladorNivelado, nivel: Optional[int]) -> None:
    if nivel is not None and nivel > n.niveles:
        raise HTTPException(422, f"El nivel tiene que ser entre 1 y {n.niveles}.")


async def actualizar_codigo(
    db: AsyncSession, slug: str, nomenclador_id: int, body: NiveladoCodigoIn,
) -> NiveladoCodigoOut:
    n = await _get(db, slug)
    _validar_nivel(n, body.nivel)
    c = (await db.execute(select(NomencladorNiveladoCodigo).where(
        NomencladorNiveladoCodigo.nomenclador_nivelado_id == n.id,
        NomencladorNiveladoCodigo.nomenclador_id == nomenclador_id,
    ))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, "El código no está en este nomenclador")
    c.nivel, c.unidades, c.observacion = body.nivel, body.unidades, body.observacion
    await db.flush()
    return _codigo_out(c, await db.get(NomencladorCMC, nomenclador_id))


async def agregar_codigo(db: AsyncSession, slug: str, body: NiveladoCodigoAltaIn) -> NiveladoCodigoOut:
    n = await _get(db, slug)
    _validar_nivel(n, body.nivel)
    nom = await db.get(NomencladorCMC, body.nomenclador_id)
    if nom is None:
        raise HTTPException(404, "Código no encontrado")
    ya = (await db.execute(select(NomencladorNiveladoCodigo.id).where(
        NomencladorNiveladoCodigo.nomenclador_nivelado_id == n.id,
        NomencladorNiveladoCodigo.nomenclador_id == nom.id,
    ))).scalar_one_or_none()
    if ya is not None:
        raise HTTPException(409, f"El código {nom.codigo} ya está en este nomenclador.")
    c = NomencladorNiveladoCodigo(
        nomenclador_nivelado_id=n.id, nomenclador_id=nom.id,
        nivel=body.nivel, unidades=body.unidades, observacion=body.observacion,
    )
    db.add(c)
    await db.flush()
    return _codigo_out(c, nom)


async def quitar_codigo(db: AsyncSession, slug: str, nomenclador_id: int) -> None:
    n = await _get(db, slug)
    c = (await db.execute(select(NomencladorNiveladoCodigo).where(
        NomencladorNiveladoCodigo.nomenclador_nivelado_id == n.id,
        NomencladorNiveladoCodigo.nomenclador_id == nomenclador_id,
    ))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, "El código no está en este nomenclador")
    await db.delete(c)
    await db.flush()


# ─── Aplicar a una obra social ───────────────────────────────────────────────

def _cantidad(nom: NomencladorCMC, galeno: Optional[Galeno], concepto: str) -> Decimal:
    """Unidades del concepto: las del galeno, si no las del código (NN), si no 0.
    Misma regla que `routes_valores._resolver_cantidad` con cantidad 0."""
    from app.modules.nomenclador.routes_valores import _resolver_cantidad

    return _resolver_cantidad(nom, galeno, concepto, Decimal("0"))


def _componentes(
    nom: NomencladorCMC, galeno: Galeno, gasto: Optional[Galeno], unidades: Optional[Decimal],
) -> list[dict]:
    hon = unidades if unidades is not None else _cantidad(nom, galeno, "Honorarios")
    ayu = Decimal("0") if unidades is not None else _cantidad(nom, galeno, "Ayudante")
    gas_galeno = gasto or galeno
    gas = _cantidad(nom, gasto, "Gastos") if gasto is not None else Decimal("0")
    return [
        {"concepto": "Honorarios", "galeno_id": galeno.id, "cantidad": hon,
         "valor_unitario": None, "orden": 0, "observacion": None},
        {"concepto": "Gastos", "galeno_id": gas_galeno.id, "cantidad": gas,
         "valor_unitario": None, "orden": 1, "observacion": None},
        {"concepto": "Ayudante", "galeno_id": galeno.id, "cantidad": ayu,
         "valor_unitario": None, "orden": 2, "observacion": None},
    ]


def _precio(comps: list[dict], galenos: dict[int, Galeno]) -> Decimal:
    total = sum(
        (Decimal(c["cantidad"]) * Decimal(galenos[c["galeno_id"]].valor_unitario) for c in comps),
        Decimal("0"),
    )
    return total.quantize(Decimal("0.01"))


async def aplicar(
    db: AsyncSession, slug: str, obra_social_nro: int, vigencia_desde: datetime.date,
    *, dry_run: bool, usuario: Optional[str],
) -> AplicarNiveladoOut:
    """Da de alta los códigos del nomenclador en la O.S. y les crea precio con el
    galeno de su nivel. Con `dry_run` sólo calcula. No hace commit."""
    from app.modules.nomenclador.routes_valores import _forzar_ayudantes_honorarios_individuales

    n = await _get(db, slug)
    g_codigo, g_nombre = await _galeno_de(db, n.galeno_grupo)
    if g_codigo is None:
        raise HTTPException(409, f"La plantilla de galeno '{n.galeno_grupo}' no existe.")
    fila_os = (await db.execute(select(ObrasSociales.OBRA_SOCIAL).where(
        ObrasSociales.NRO_OBRASOCIAL == obra_social_nro
    ))).first()
    if fila_os is None:
        raise HTTPException(404, "Obra social no encontrada")
    os_nombre = (fila_os.OBRA_SOCIAL or "").strip() or f"La obra social {obra_social_nro}"

    codigos = list((await db.execute(
        select(NomencladorNiveladoCodigo, NomencladorCMC)
        .join(NomencladorCMC, NomencladorCMC.id == NomencladorNiveladoCodigo.nomenclador_id)
        .options(selectinload(NomencladorCMC.nomenclador_nacional))
        .where(NomencladorNiveladoCodigo.nomenclador_nivelado_id == n.id)
        .order_by(NomencladorCMC.codigo)
    )).all())

    # 1. Galenos de la O.S.: todos los niveles que usa el nomenclador, vigentes.
    galenos_os = {
        g.nivel: g for g in (await db.execute(select(Galeno).where(
            Galeno.obra_social_nro == obra_social_nro, Galeno.codigo == g_codigo,
            Galeno.activo == True, Galeno.nivel.is_not(None),
        ))).scalars()
    }
    if galenos_os and len(galenos_os) != n.niveles:
        raise HTTPException(409, {
            "mensaje": f"{os_nombre} tiene el {g_nombre} de {len(galenos_os)} niveles y este "
                       f"nomenclador es de {n.niveles}.",
            "faltan": [],
        })
    usados = {c.nivel if c.nivel is not None else 1 for c, _ in codigos}
    faltan = sorted(usados - set(galenos_os))
    if faltan:
        raise HTTPException(409, {
            "mensaje": f"{os_nombre} no tiene vigente el {g_nombre} en "
                       f"{'el nivel' if len(faltan) == 1 else 'los niveles'} "
                       f"{', '.join(map(str, faltan))}. Cargalo en Galenos y volvé a aplicar.",
            "faltan": faltan,
        })
    gasto = (await db.execute(select(Galeno).where(
        Galeno.obra_social_nro == obra_social_nro, Galeno.codigo == GASTO_QUIRURGICO,
        Galeno.activo == True, Galeno.nivel.is_(None),
    ))).scalars().first()
    galenos_por_id = {g.id: g for g in galenos_os.values()}
    if gasto is not None:
        galenos_por_id[gasto.id] = gasto

    # 2. Estado de cada código en la O.S.
    nom_ids = [nom.id for _, nom in codigos]
    cods = [nom.codigo for _, nom in codigos]
    con_precio = set((await db.execute(select(Valor.nomenclador_id).where(
        Valor.obra_social_nro == obra_social_nro, Valor.nomenclador_id.in_(nom_ids),
        Valor.estado == "activo",
    ))).scalars()) if nom_ids else set()
    pares = {
        p.nomenclador_id: p for p in (await db.execute(select(CodigoObraSocial).where(
            CodigoObraSocial.obra_social_nro == obra_social_nro,
            CodigoObraSocial.nomenclador_id.in_(nom_ids),
        ))).scalars()
    } if nom_ids else {}

    def _clasificar(c, nom) -> Optional[tuple[str, str]]:
        if not nom.activo:
            return "omitido", "El código está inactivo en el catálogo"
        if nom.id in con_precio:
            return "ya_tiene_precio", "Ya tiene precio en esta obra social"
        par = pares.get(nom.id)
        if par is not None and par.estado == "suspendido":
            return "suspendido", "El código está suspendido en esta obra social"
        return None

    a_cotizar = [(c, nom) for c, nom in codigos if _clasificar(c, nom) is None]

    # 3. Alta de los que no la tienen (copia la plantilla de quién factura).
    altas = [nom for _, nom in a_cotizar if nom.id not in pares]
    if altas and not dry_run:
        await alta_os.dar_de_alta(db, AltaCodigosIn(items=[
            AltaCodigoItem(obra_social_nro=obra_social_nro, nomenclador_id=nom.id) for nom in altas
        ]), usuario)
        pares = {
            p.nomenclador_id: p for p in (await db.execute(select(CodigoObraSocial).where(
                CodigoObraSocial.obra_social_nro == obra_social_nro,
                CodigoObraSocial.nomenclador_id.in_(nom_ids),
            ))).scalars()
        }

    # 4. Quién factura cada uno (lo configurado en la O.S.; sin alta, la plantilla).
    habilitadas: dict[str, list[int]] = {}
    for cod, esp in (await db.execute(select(
        ValorEspecialidad.codigo, ValorEspecialidad.especialidad_id_colegio
    ).where(
        ValorEspecialidad.obra_social_nro == obra_social_nro, ValorEspecialidad.codigo.in_(cods),
    ))).all() if cods else []:
        habilitadas.setdefault(cod, []).append(esp)
    plantillas: dict[str, list[int]] = {}
    for cod, esp in (await db.execute(select(
        NomencladorPlantillaEspecialidad.codigo, NomencladorPlantillaEspecialidad.especialidad_id_colegio,
    ).where(NomencladorPlantillaEspecialidad.codigo.in_(cods)))).all() if cods else []:
        plantillas.setdefault(cod, []).append(esp)

    def _quien_factura(nom) -> tuple[bool, list[int]]:
        par = pares.get(nom.id)
        ya = sorted(habilitadas.get(nom.codigo, []))
        if par is not None:
            return bool(par.sin_restriccion_especialidad), ya
        # Sin alta (sólo en la vista previa): lo que haría `dar_de_alta`.
        if ya:
            return False, ya
        if nom.sin_restriccion_especialidad:
            return True, []
        return False, sorted(plantillas.get(nom.codigo, []))

    # 5. Precios.
    filas_out: list[AplicarNiveladoFila] = []
    a_crear: list[tuple[dict, list[dict]]] = []
    for c, nom in codigos:
        base = dict(
            nomenclador_id=nom.id, codigo=nom.codigo, descripcion=nom.descripcion,
            nivel=c.nivel, unidades=c.unidades,
        )
        clasif = _clasificar(c, nom)
        if clasif is not None:
            filas_out.append(AplicarNiveladoFila(**base, estado=clasif[0], motivo=clasif[1]))
            continue
        sin_restr, esps = _quien_factura(nom)
        if not sin_restr and not esps:
            filas_out.append(AplicarNiveladoFila(
                **base, estado="sin_quien_factura",
                motivo="Se da de alta sin precio: no tiene especialidades que lo facturen",
            ))
            continue
        galeno = galenos_os[c.nivel if c.nivel is not None else 1]
        comps = _componentes(nom, galeno, gasto, c.unidades)
        par = pares.get(nom.id)
        categoria = par.categoria if par else None
        variantes = [None] if sin_restr else esps
        for esp in variantes:
            a_crear.append(({
                "obra_social_nro": obra_social_nro, "nomenclador_id": nom.id, "origen": "NE",
                "codigo": nom.codigo,
                "descripcion": (par.descripcion if par and par.descripcion else nom.descripcion),
                "nivel": galeno.nivel, "complejidad": par.complejidad if par else None,
                "categoria": categoria,
                "requiere_autorizacion": par.requiere_autorizacion if par else None,
                "especialidad_id_colegio": esp, "sin_restriccion_especialidad": sin_restr,
                "por_presupuesto": False,
                "cantidad_ayudantes": _forzar_ayudantes_honorarios_individuales(
                    categoria, nom, par.cantidad_ayudantes if par else None,
                ),
                "coseguro": Decimal("0"), "vigencia_desde": vigencia_desde, "observacion": None,
            }, comps))
        filas_out.append(AplicarNiveladoFila(
            **base, estado="crear" if dry_run else "creado",
            precios=len(variantes), precio=_precio(comps, galenos_por_id),
        ))

    if a_crear and not dry_run:
        await service.persistir_valores_en_bloque(db, a_crear, motivo="carga_inicial")

    def cuenta(estado: str) -> int:
        return sum(1 for f in filas_out if f.estado == estado)

    return AplicarNiveladoOut(
        dry_run=dry_run, obra_social_nro=obra_social_nro, nomenclador=n.nombre,
        galeno_nombre=g_nombre or g_codigo,
        resumen=AplicarNiveladoResumen(
            total=len(filas_out), crear=cuenta("crear") + cuenta("creado"),
            ya_tiene_precio=cuenta("ya_tiene_precio"), sin_quien_factura=cuenta("sin_quien_factura"),
            suspendido=cuenta("suspendido"), omitido=cuenta("omitido"),
            precios=len(a_crear), altas=len(altas),
        ),
        filas=filas_out,
    )
