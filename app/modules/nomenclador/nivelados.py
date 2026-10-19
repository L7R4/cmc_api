"""Nomencladores nivelados: Cirugía adulto 7 y 10 niveles, Cirugía infantil 7,
FASGO 13, Urología 7…

Casi todas las obras sociales comparten el mismo nomenclador: un código tiene el
mismo nivel (o las mismas N unidades fijas) en todas; lo que cambia es el precio del
galeno por nivel, que pacta cada una. El nomenclador va ligado a un grupo de
`nm_galenos_plantilla` (de ahí sale el galeno nivelado con el que se cotiza).

Aplicarlo a una obra social (`aplicar`) da de alta cada código con su plantilla de
quién factura y crea un precio NE por especialidad. El nomenclador puede acotar las
especialidades (`especialidades`, vacío = todas): sólo se carga precio en las que están en
su lista y habilitadas para el código en la O.S.; el precio de las demás no se toca. Un código
"sin restricción de especialidad" sigue sin restricción (su NN vale para cualquier médico) y recibe
un precio NE por cada especialidad del nomenclador; si el nomenclador no tiene lista, se cotiza con
un único precio NE sin especialidad:
  - Honorarios: el galeno del nivel del código, con las unidades del galeno.
    Los de "N unidades": el galeno de nivel 1 con cantidad N.
  - Ayudante: el mismo galeno, con sus unidades de ayudante (los de unidades, 0).
  - Gastos: `gasto_quirurgico` de la O.S. con las unidades del código, si lo tiene.
Las unidades por nivel salen de la ESCALA del nomenclador (`nm_nomenclador_nivel`, ver
`motor_nomenclador`), salvo los niveles que la O.S. ajustó en su galeno. Si la O.S. todavía no
tiene el galeno, o lo tiene de otra cantidad de niveles (Cirugía adulto 7 ↔ 10), `aplicar` lo
crea con los datos de `galeno` (valor unitario, o precio por nivel); en el cambio de
nomenclador se eliminan los valores del anterior y se crean los del nuevo.
Lo que ya tiene precio NE en la O.S. recibe la vigencia igual: si la fecha es
posterior rota, si es la misma la reemplaza y si es anterior borra de la base las
vigencias más nuevas (`service.liberar_vigencia`). Un NN no se toca. Ver `seed_nomencladores_nivelados.py`
para la carga inicial.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.catalogs import Especialidad, ObrasSociales
from app.db.models.nomenclador_cmc import (
    CodigoObraSocial,
    Galeno,
    GalenoPlantilla,
    NomencladorCMC,
    NomencladorNivel,
    NomencladorNivelado,
    NomencladorNiveladoCodigo,
    NomencladorPlantillaEspecialidad,
    Valor,
    ValorComponente,
    ValorEspecialidad,
)
from app.modules.nomenclador import alta_os, motor_nomenclador as motor, service
from app.modules.nomenclador.schemas import (
    ComponenteNNSugeridoOut,
    ComponentesNNSugeridosOut,
    AltaCodigoItem,
    AltaCodigosIn,
    AplicarNiveladoCambio,
    AplicarNiveladoFila,
    AplicarNiveladoGaleno,
    AplicarNiveladoOut,
    AplicarNiveladoResumen,
    NiveladoEscalaIn,
    NiveladoEscalaNivel,
    NiveladoEscalaOut,
    NiveladoEstadoOsOut,
    GalenoResumenOut,
    NiveladoGalenoNivel,
    NomencladorDeCodigoOut,
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
        total = sum(por_nivel.values()) + con_unidades
        if n.tipo == "nacional":
            total = (await db.execute(
                select(func.count()).select_from(NomencladorCMC).where(
                    NomencladorCMC.nomenclador_nacional_id.is_not(None), NomencladorCMC.activo == True,  # noqa: E712
                )
            )).scalar_one()
        out.append(NomencladorNiveladoOut(
            id=n.id, slug=n.slug, nombre=n.nombre, galeno_grupo=n.galeno_grupo,
            galeno_codigo=n.galeno_codigo or g_codigo, galeno_nombre=g_nombre,
            galeno_gastos_codigo=n.galeno_gastos_codigo, tipo=n.tipo, niveles=n.niveles,
            especialidades=sorted(n.especialidades or []),
            total_codigos=total, por_nivel=por_nivel, con_unidades=con_unidades,
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

async def actualizar_especialidades(db: AsyncSession, slug: str, especialidades: list[int]) -> list[int]:
    """Define a qué especialidades se aplica el nomenclador. Vacío = todas las del código."""
    n = await _get(db, slug)
    ids = sorted(set(especialidades))
    if ids:
        existentes = set((await db.execute(
            select(Especialidad.ID_COLEGIO_ESPE).where(Especialidad.ID_COLEGIO_ESPE.in_(ids))
        )).scalars())
        faltan = [i for i in ids if i not in existentes]
        if faltan:
            raise HTTPException(422, f"Especialidades inexistentes: {', '.join(map(str, faltan))}.")
    n.especialidades = ids or None
    await db.flush()
    return ids


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


# ─── Escala ──────────────────────────────────────────────────────────────────

async def obtener_escala(db: AsyncSession, slug: str) -> NiveladoEscalaOut:
    n = await _get(db, slug)
    escala = await motor.escala_de(db, n.id)
    g_codigo = n.galeno_codigo or (await _galeno_de(db, n.galeno_grupo))[0]
    cuenta = 0
    if g_codigo and n.niveles:
        cuenta = (await db.execute(
            select(func.count()).select_from(
                select(Galeno.obra_social_nro).where(
                    Galeno.codigo == g_codigo, Galeno.activo == True, Galeno.nivel.is_not(None),  # noqa: E712
                ).group_by(Galeno.obra_social_nro).having(func.count() == n.niveles).subquery()
            )
        )).scalar_one()
    return NiveladoEscalaOut(
        slug=n.slug, nombre=n.nombre, galeno_codigo=g_codigo, galeno_gastos_codigo=n.galeno_gastos_codigo,
        niveles=[
            NiveladoEscalaNivel(nivel=nv, unidades_honorarios=uh, unidades_ayudante=ua)
            for nv, (uh, ua) in sorted(escala.items())
        ],
        obras_sociales=cuenta,
    )


async def guardar_escala(db: AsyncSession, slug: str, body: NiveladoEscalaIn) -> NiveladoEscalaOut:
    """Reemplaza la escala del nomenclador. No toca ningún valor: para llevarla a las
    obras sociales, `recalcular`."""
    n = await _get(db, slug)
    if n.tipo != "nivelado":
        raise HTTPException(409, "El Nomenclador Nacional no tiene escala por nivel.")
    niveles = sorted(x.nivel for x in body.niveles)
    if niveles != list(range(1, n.niveles + 1)):
        raise HTTPException(422, f"La escala tiene que tener los niveles 1 a {n.niveles}, sin repetir.")
    existentes = {
        f.nivel: f for f in (await db.execute(
            select(NomencladorNivel).where(NomencladorNivel.nomenclador_nivelado_id == n.id)
        )).scalars()
    }
    for x in body.niveles:
        f = existentes.get(x.nivel)
        if f is None:
            db.add(NomencladorNivel(
                nomenclador_nivelado_id=n.id, nivel=x.nivel,
                unidades_honorarios=x.unidades_honorarios, unidades_ayudante=x.unidades_ayudante,
            ))
        else:
            f.unidades_honorarios, f.unidades_ayudante = x.unidades_honorarios, x.unidades_ayudante
    await db.flush()
    return await obtener_escala(db, slug)


async def recalcular(
    db: AsyncSession, slug: str, vigencia_desde: datetime.date, *, dry_run: bool, usuario: Optional[str],
) -> dict:
    """Vuelve a calcular los valores del nomenclador en TODAS las obras sociales que tienen su
    galeno (misma cantidad de niveles), con la escala actual y desde `vigencia_desde`. Los
    niveles ajustados por una obra social conservan su ajuste. Una obra social que no se
    puede recalcular (p. ej. vigencia anterior a algo) se informa y no frena a las demás."""
    n = await _get(db, slug)
    if n.tipo != "nivelado":
        raise HTTPException(409, "El Nomenclador Nacional no se recalcula desde acá.")
    g_codigo = n.galeno_codigo or (await _galeno_de(db, n.galeno_grupo))[0]
    os_nros = [r[0] for r in (await db.execute(
        select(Galeno.obra_social_nro).where(
            Galeno.codigo == g_codigo, Galeno.activo == True, Galeno.nivel.is_not(None),  # noqa: E712
        ).group_by(Galeno.obra_social_nro).having(func.count() == n.niveles).order_by(Galeno.obra_social_nro)
    )).all()]
    resultados, errores = [], []
    for nro in os_nros:
        try:
            async with db.begin_nested():
                out = await aplicar(db, slug, nro, vigencia_desde, dry_run=dry_run, usuario=usuario)
            resultados.append({
                "obra_social_nro": nro, "valores": out.resumen.precios,
                "crear": out.resumen.crear, "reemplazar": out.resumen.reemplazar,
            })
        except HTTPException as e:
            errores.append({"obra_social_nro": nro, "motivo": e.detail if isinstance(e.detail, str) else (e.detail.get("mensaje") or "No se pudo recalcular")})
    return {
        "dry_run": dry_run, "obras_sociales": len(resultados), "valores": sum(r["valores"] for r in resultados),
        "detalle": resultados, "errores": errores,
    }


# ─── Galeno de la O.S. ───────────────────────────────────────────────────────

async def _galenos_vigentes(db: AsyncSession, obra_social_nro: int, g_codigo: str) -> dict[int, Galeno]:
    return {
        g.nivel: g for g in (await db.execute(select(Galeno).where(
            Galeno.obra_social_nro == obra_social_nro, Galeno.codigo == g_codigo,
            Galeno.activo == True, Galeno.nivel.is_not(None),  # noqa: E712
        ))).scalars()
    }


def _validar_cfg_galeno(cfg: Optional[AplicarNiveladoGaleno], n: NomencladorNivelado) -> AplicarNiveladoGaleno:
    """422 si falta el precio del galeno que hay que crear."""
    if cfg is None:
        raise HTTPException(422, "Falta el precio del galeno de esta obra social.")
    niveles = range(1, n.niveles + 1)
    if cfg.precio_por_nivel:
        precios = cfg.precios_por_nivel or {}
        faltan = [nv for nv in niveles if not precios.get(nv) or Decimal(precios[nv]) <= 0]
        if faltan:
            raise HTTPException(422, f"Falta el precio de los niveles: {', '.join(map(str, faltan))}.")
    elif cfg.valor_unitario is None:
        raise HTTPException(422, "Falta el valor unitario del galeno.")
    invalidos = [a.nivel for a in cfg.ajustes if a.nivel not in niveles]
    if invalidos:
        raise HTTPException(422, f"Ajuste de unidades en niveles que no existen: {', '.join(map(str, invalidos))}.")
    return cfg


def _filas_galeno_nuevo(
    n: NomencladorNivelado, g_codigo: str, g_nombre: str, obra_social_nro: int,
    vigencia_desde: datetime.date, cfg: AplicarNiveladoGaleno,
) -> dict[int, Galeno]:
    """Los `Galeno` del nomenclador para esta O.S. (sin agregar a la sesión)."""
    ajustes = {a.nivel: a for a in cfg.ajustes}
    out = {}
    for nivel in range(1, n.niveles + 1):
        aj = ajustes.get(nivel)
        if cfg.precio_por_nivel:
            uh, ua = Decimal("1"), motor.AYUDANTE_PCT
            vu = Decimal(cfg.precios_por_nivel[nivel])
        else:
            uh = aj.unidades_honorarios if aj and aj.unidades_honorarios is not None else None
            ua = aj.unidades_ayudante if aj and aj.unidades_ayudante is not None else None
            vu = Decimal(cfg.valor_unitario)
        out[nivel] = Galeno(
            obra_social_nro=obra_social_nro, codigo=g_codigo, nombre=g_nombre, nivel=nivel,
            vigencia_desde=vigencia_desde, vigencia_hasta=None, valor_unitario=vu,
            unidades_honorarios=uh, unidades_ayudante=ua, precio_por_nivel=cfg.precio_por_nivel,
            activo=True, visible=True,
        )
    return out


def _con_ajustes(galeno: Galeno, aj, persistir: bool) -> Galeno:
    """El galeno con el ajuste de unidades aplicado. Sin `persistir`, una copia que no toca la sesión."""
    uh = aj.unidades_honorarios if aj.unidades_honorarios is not None else galeno.unidades_honorarios
    ua = aj.unidades_ayudante if aj.unidades_ayudante is not None else galeno.unidades_ayudante
    if persistir:
        galeno.unidades_honorarios, galeno.unidades_ayudante = uh, ua
        return galeno
    return Galeno(
        id=galeno.id, obra_social_nro=galeno.obra_social_nro, codigo=galeno.codigo, nombre=galeno.nombre,
        nivel=galeno.nivel, vigencia_desde=galeno.vigencia_desde, valor_unitario=galeno.valor_unitario,
        unidades_honorarios=uh, unidades_ayudante=ua, unidades_gastos=galeno.unidades_gastos,
        precio_por_nivel=galeno.precio_por_nivel, activo=galeno.activo,
    )


async def _valores_calculados_con(
    db: AsyncSession, obra_social_nro: int, galeno_ids: list[int],
) -> dict[tuple[int, Optional[int]], int]:
    """Valores ACTIVOS de la O.S. con algún componente calculado con esos galenos:
    `{(nomenclador_id, especialidad): cantidad}`."""
    if not galeno_ids:
        return {}
    filas = (await db.execute(
        select(Valor.nomenclador_id, Valor.especialidad_id_colegio, func.count(func.distinct(Valor.id)))
        .join(ValorComponente, ValorComponente.valor_id == Valor.id)
        .where(
            Valor.obra_social_nro == obra_social_nro, Valor.estado == "activo",
            ValorComponente.activo == True, ValorComponente.galeno_id.in_(galeno_ids),  # noqa: E712
        ).group_by(Valor.nomenclador_id, Valor.especialidad_id_colegio)
    )).all()
    return {(nom_id, esp): c for nom_id, esp, c in filas}


async def _nomenclador_anterior(
    db: AsyncSession, n: NomencladorNivelado, g_codigo: str, niveles_os: int,
) -> Optional[NomencladorNivelado]:
    return next(
        (m for m in await motor.nomenclador_por_galeno(db, g_codigo) if m.id != n.id and m.niveles == niveles_os),
        None,
    )


async def _cambio(
    db: AsyncSession, n: NomencladorNivelado, viejo: NomencladorNivelado, galenos_os: dict[int, Galeno],
    obra_social_nro: int,
) -> tuple[AplicarNiveladoCambio, dict[tuple[int, Optional[int]], int], set[int]]:
    """Qué se elimina al pasar la O.S. de `viejo` a `n`: `(resumen, valores_del_viejo, ids_codigos_viejo)`.
    409 si hay valores fuera del nomenclador viejo que usan ese galeno (se quedarían sin precio)."""
    ids_viejo = {
        r[0] for r in (await db.execute(
            select(NomencladorNiveladoCodigo.nomenclador_id)
            .where(NomencladorNiveladoCodigo.nomenclador_nivelado_id == viejo.id)
        )).all()
    }
    ids_nuevo = {
        r[0] for r in (await db.execute(
            select(NomencladorNiveladoCodigo.nomenclador_id)
            .where(NomencladorNiveladoCodigo.nomenclador_nivelado_id == n.id)
        )).all()
    }
    usados = await _valores_calculados_con(db, obra_social_nro, [g.id for g in galenos_os.values()])
    ajenos = sorted({nom_id for nom_id, _ in usados} - ids_viejo)
    if ajenos:
        codigos = [r[0] for r in (await db.execute(
            select(NomencladorCMC.codigo).where(NomencladorCMC.id.in_(ajenos)).order_by(NomencladorCMC.codigo).limit(8)
        )).all()]
        raise HTTPException(409, {
            "mensaje": (
                f"No se puede cambiar de nomenclador: hay {len(ajenos)} código(s) que no son de "
                f"{viejo.nombre} y se calculan con su galeno ({', '.join(codigos)}"
                f"{'…' if len(ajenos) > len(codigos) else ''}). Pasalos a otro tipo de valor primero."
            ),
            "faltan": [],
        })
    return AplicarNiveladoCambio(
        desde_slug=viejo.slug, desde_nombre=viejo.nombre, niveles_desde=viejo.niveles,
        valores_eliminados=sum(usados.values()), codigos_sin_reemplazo=len(ids_viejo - ids_nuevo),
    ), usados, ids_viejo


# ─── Resumen de galenos de una O.S. ──────────────────────────────────────────

async def resumen_galenos(db: AsyncSession, obra_social_nro: int) -> list[GalenoResumenOut]:
    """Por cada galeno vigente de la O.S.: nomencladores que lo usan, modo de precio y, en los
    nivelados, las unidades efectivas por nivel y cuáles se apartan de la escala."""
    vigentes = list((await db.execute(
        select(Galeno).where(Galeno.obra_social_nro == obra_social_nro, Galeno.activo == True)  # noqa: E712
        .order_by(Galeno.codigo, Galeno.nivel)
    )).scalars())
    noms = list((await db.execute(
        select(NomencladorNivelado).where(NomencladorNivelado.tipo == "nivelado", NomencladorNivelado.activo == True)  # noqa: E712
        .order_by(NomencladorNivelado.nombre)
    )).scalars())
    base_nn = set(service._GALENOS_NN_CODIGOS)
    por_codigo: dict[str, list[Galeno]] = {}
    for g in vigentes:
        por_codigo.setdefault(g.codigo, []).append(g)
    out = []
    for codigo, filas in por_codigo.items():
        niveladas = [g for g in filas if g.nivel is not None]
        usados = [
            m for m in noms
            if m.galeno_codigo == codigo or (m.galeno_gastos_codigo == codigo and not niveladas)
        ]
        nombres = [m.nombre for m in usados] + (["Nacional"] if codigo in base_nn else [])
        item = GalenoResumenOut(
            codigo=codigo, nomencladores=nombres,
            modo="por_nivel" if any(g.precio_por_nivel for g in filas) else "calculado",
        )
        propio = next((m for m in usados if m.galeno_codigo == codigo and m.niveles == len(niveladas)), None)
        if niveladas and propio is not None:
            escala = await motor.escala_de(db, propio.id)
            for g in sorted(niveladas, key=lambda x: x.nivel):
                uh, ua = motor.unidades_de_nivel(escala, g.nivel, g)
                esc_h, esc_a = escala.get(g.nivel, (None, None))
                esc_a = esc_a if esc_a is not None else (motor.ayudante_por_defecto(esc_h) if esc_h is not None else None)
                item.niveles.append(NiveladoGalenoNivel(
                    nivel=g.nivel, valor_unitario=g.valor_unitario, unidades_honorarios=uh, unidades_ayudante=ua,
                    ajustado=not g.precio_por_nivel and esc_h is not None and (uh != esc_h or ua != esc_a),
                ))
        out.append(item)
    return out


# ─── Alta de un valor "por nomenclador" ──────────────────────────────────────

async def nomencladores_de_codigo(db: AsyncSession, nomenclador_id: int) -> list[NomencladorDeCodigoOut]:
    """Los nomencladores con los que se puede facturar un código: aquellos que lo contienen, y el
    Nacional si el código tiene equivalente nacional."""
    out = [
        NomencladorDeCodigoOut(
            id=nv.id, slug=nv.slug, nombre=nv.nombre, tipo=nv.tipo, niveles=nv.niveles,
            nivel_del_codigo=c.nivel, unidades_del_codigo=c.unidades,
        )
        for c, nv in (await db.execute(
            select(NomencladorNiveladoCodigo, NomencladorNivelado)
            .join(NomencladorNivelado, NomencladorNivelado.id == NomencladorNiveladoCodigo.nomenclador_nivelado_id)
            .where(NomencladorNiveladoCodigo.nomenclador_id == nomenclador_id, NomencladorNivelado.activo == True)  # noqa: E712
            .order_by(NomencladorNivelado.nombre)
        )).all()
    ]
    nom = await db.get(NomencladorCMC, nomenclador_id)
    if nom is not None and nom.nomenclador_nacional_id is not None:
        nac = (await db.execute(select(NomencladorNivelado).where(NomencladorNivelado.tipo == "nacional"))).scalars().first()
        if nac is not None:
            out.insert(0, NomencladorDeCodigoOut(id=nac.id, slug=nac.slug, nombre=nac.nombre, tipo=nac.tipo, niveles=0))
    return out


async def componentes_para_codigo(
    db: AsyncSession, slug: str, obra_social_nro: int, nomenclador_id: int,
) -> ComponentesNNSugeridosOut:
    """Componentes de un valor de ese código que se factura por el nomenclador `slug` en la O.S.
    (para precargar el alta). Todo motivo para no poder armarlos sale con `disponible=False`."""
    n = await _get(db, slug)
    if n.tipo == "nacional":
        try:
            comps = await service.componentes_nn_sugeridos(db, obra_social_nro, nomenclador_id)
        except service.ComponentesNNNoDisponibles as e:
            return ComponentesNNSugeridosOut(disponible=False, motivo=str(e), nomenclador_nivelado_id=n.id)
        nombres = {g.id: g.nombre for g in (await db.execute(
            select(Galeno).where(Galeno.id.in_({c["galeno_id"] for c in comps}))
        )).scalars()}
        return ComponentesNNSugeridosOut(
            disponible=True, nomenclador_nivelado_id=n.id,
            componentes=[ComponenteNNSugeridoOut(**c, galeno_nombre=nombres.get(c["galeno_id"], "")) for c in comps],
        )
    nom = await db.get(NomencladorCMC, nomenclador_id)
    fila = (await db.execute(
        select(NomencladorNiveladoCodigo).where(
            NomencladorNiveladoCodigo.nomenclador_nivelado_id == n.id,
            NomencladorNiveladoCodigo.nomenclador_id == nomenclador_id,
        )
    )).scalars().first()
    if nom is None or fila is None:
        return ComponentesNNSugeridosOut(
            disponible=False, nomenclador_nivelado_id=n.id, motivo=f"El código no pertenece a {n.nombre}.",
        )
    g_codigo = n.galeno_codigo or (await _galeno_de(db, n.galeno_grupo))[0]
    galenos_os = await _galenos_vigentes(db, obra_social_nro, g_codigo)
    nivel = fila.nivel if fila.nivel is not None else 1
    if len(galenos_os) != n.niveles or nivel not in galenos_os:
        return ComponentesNNSugeridosOut(
            disponible=False, nomenclador_nivelado_id=n.id,
            motivo=(f"La obra social no tiene el galeno de {n.nombre} cargado. "
                    "Aplicá el nomenclador a la obra social desde Nomencladores."),
        )
    gasto = (await db.execute(select(Galeno).where(
        Galeno.obra_social_nro == obra_social_nro, Galeno.codigo == (n.galeno_gastos_codigo or GASTO_QUIRURGICO),
        Galeno.activo == True, Galeno.nivel.is_(None),  # noqa: E712
    ))).scalars().first()
    escala = await motor.escala_de(db, n.id)
    comps, galeno = motor.componentes_de_codigo(nom, fila.nivel, fila.unidades, escala, galenos_os, gasto)
    nombres = {galeno.id: galeno.nombre, **({gasto.id: gasto.nombre} if gasto is not None else {})}
    return ComponentesNNSugeridosOut(
        disponible=True, nivel=nivel, nomenclador_nivelado_id=n.id,
        precio_por_nivel=bool(galeno.precio_por_nivel),
        componentes=[
            ComponenteNNSugeridoOut(
                concepto=c["concepto"], galeno_id=c["galeno_id"], galeno_nombre=nombres.get(c["galeno_id"], ""),
                cantidad=c["cantidad"], orden=c["orden"],
            ) for c in comps
        ],
    )


# ─── Estado de la O.S. ───────────────────────────────────────────────────────

async def estado_os(db: AsyncSession, slug: str, obra_social_nro: int) -> NiveladoEstadoOsOut:
    """Qué tiene la O.S. del galeno de este nomenclador (para armar el modal de aplicar)."""
    n = await _get(db, slug)
    if n.tipo != "nivelado":
        raise HTTPException(409, "El Nomenclador Nacional no se aplica a una obra social.")
    fila_os = (await db.execute(select(ObrasSociales.OBRA_SOCIAL).where(
        ObrasSociales.NRO_OBRASOCIAL == obra_social_nro
    ))).first()
    if fila_os is None:
        raise HTTPException(404, "Obra social no encontrada")
    g_codigo = n.galeno_codigo or (await _galeno_de(db, n.galeno_grupo))[0]
    galenos_os = await _galenos_vigentes(db, obra_social_nro, g_codigo)
    out = NiveladoEstadoOsOut(
        obra_social_nro=obra_social_nro, obra_social=(fila_os.OBRA_SOCIAL or "").strip(),
        niveles_nomenclador=n.niveles, galeno_estado="falta", niveles_os=len(galenos_os) or None,
    )
    if not galenos_os:
        return out
    escala = await motor.escala_de(db, n.id)
    if len(galenos_os) == n.niveles:
        out.galeno_estado = "ok"
        out.precio_por_nivel = any(g.precio_por_nivel for g in galenos_os.values())
        for nivel, g in sorted(galenos_os.items()):
            uh, ua = motor.unidades_de_nivel(escala, nivel, g)
            esc_h, esc_a = escala.get(nivel, (None, None))
            esc_a = esc_a if esc_a is not None else (motor.ayudante_por_defecto(esc_h) if esc_h is not None else None)
            out.niveles.append(NiveladoGalenoNivel(
                nivel=nivel, valor_unitario=g.valor_unitario, unidades_honorarios=uh, unidades_ayudante=ua,
                ajustado=not g.precio_por_nivel and esc_h is not None and (uh != esc_h or ua != esc_a),
            ))
        return out
    out.galeno_estado = "otros_niveles"
    out.precio_por_nivel = any(g.precio_por_nivel for g in galenos_os.values())
    viejo = await _nomenclador_anterior(db, n, g_codigo, len(galenos_os))
    if viejo is not None:
        out.cambio, _, _ = await _cambio(db, n, viejo, galenos_os, obra_social_nro)
        out.cambio.valores_a_crear = (await db.execute(
            select(func.count()).select_from(NomencladorNiveladoCodigo)
            .where(NomencladorNiveladoCodigo.nomenclador_nivelado_id == n.id)
        )).scalar_one()
    return out


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
    *, dry_run: bool, usuario: Optional[str], galeno: Optional[AplicarNiveladoGaleno] = None,
) -> AplicarNiveladoOut:
    """Da de alta los códigos del nomenclador en la O.S. y les crea precio con el
    galeno de su nivel. Con `dry_run` sólo calcula. No hace commit.

    `galeno` se usa si la O.S. no tiene el galeno de este nomenclador (o lo tiene de otra
    cantidad de niveles, p. ej. pasa de Cirugía adulto 7 a 10): se crea con su precio y, en el
    cambio, se eliminan los valores del nomenclador anterior. Con el galeno ya cargado sólo
    importan sus `ajustes` de unidades."""
    from app.modules.nomenclador.routes_valores import _forzar_ayudantes_honorarios_individuales

    n = await _get(db, slug)
    if n.tipo != "nivelado":
        raise HTTPException(409, "El Nomenclador Nacional no se aplica a una obra social: sus valores se generan por rango de código.")
    g_codigo, g_nombre = await _galeno_de(db, n.galeno_grupo)
    if g_codigo is None:
        raise HTTPException(409, f"La plantilla de galeno '{n.galeno_grupo}' no existe.")
    g_codigo = n.galeno_codigo or g_codigo
    fila_os = (await db.execute(select(ObrasSociales.OBRA_SOCIAL).where(
        ObrasSociales.NRO_OBRASOCIAL == obra_social_nro
    ))).first()
    if fila_os is None:
        raise HTTPException(404, "Obra social no encontrada")
    os_nombre = (fila_os.OBRA_SOCIAL or "").strip() or f"La obra social {obra_social_nro}"

    # Especialidades a las que se aplica el nomenclador (vacío = todas las del código).
    del_nomenclador = set(n.especialidades or [])

    codigos = list((await db.execute(
        select(NomencladorNiveladoCodigo, NomencladorCMC)
        .join(NomencladorCMC, NomencladorCMC.id == NomencladorNiveladoCodigo.nomenclador_id)
        .options(selectinload(NomencladorCMC.nomenclador_nacional))
        .where(NomencladorNiveladoCodigo.nomenclador_nivelado_id == n.id)
        .order_by(NomencladorCMC.codigo)
    )).all())

    # 1. Galenos de la O.S.: todos los niveles que usa el nomenclador, vigentes.
    escala = await motor.escala_de(db, n.id)
    galenos_os = await _galenos_vigentes(db, obra_social_nro, g_codigo)
    cambio: Optional[AplicarNiveladoCambio] = None
    viejo_usados: dict[tuple[int, Optional[int]], int] = {}
    galeno_creado = False
    if galenos_os and len(galenos_os) != n.niveles:
        # Cambio de nomenclador (mismo galeno, otra cantidad de niveles).
        viejo = await _nomenclador_anterior(db, n, g_codigo, len(galenos_os))
        if viejo is None or galeno is None:
            raise HTTPException(409, {
                "mensaje": f"{os_nombre} tiene el {g_nombre} de {len(galenos_os)} niveles y este "
                           f"nomenclador es de {n.niveles}.",
                "faltan": [], "requiere_galeno": viejo is not None,
            })
        cfg = _validar_cfg_galeno(galeno, n)
        desactual = [g for g in galenos_os.values() if g.vigencia_desde >= vigencia_desde]
        if desactual:
            raise HTTPException(422, (
                f"La vigencia tiene que ser posterior al {max(g.vigencia_desde for g in desactual):%d/%m/%Y}, "
                f"que es desde cuando rige el galeno actual."
            ))
        cambio, viejo_usados, _ = await _cambio(db, n, viejo, galenos_os, obra_social_nro)
        if not dry_run:
            for g in galenos_os.values():
                g.vigencia_hasta, g.activo = vigencia_desde - datetime.timedelta(days=1), False
            await db.flush()
        galenos_os = _filas_galeno_nuevo(n, g_codigo, g_nombre or g_codigo, obra_social_nro, vigencia_desde, cfg)
        galeno_creado = True
    elif not galenos_os and galeno is not None:
        cfg = _validar_cfg_galeno(galeno, n)
        galenos_os = _filas_galeno_nuevo(n, g_codigo, g_nombre or g_codigo, obra_social_nro, vigencia_desde, cfg)
        galeno_creado = True
    elif galeno is not None and galeno.ajustes:
        por_nivel = {a.nivel: a for a in galeno.ajustes}
        galenos_os = {
            nv: _con_ajustes(g, por_nivel[nv], persistir=not dry_run) if nv in por_nivel else g
            for nv, g in galenos_os.items()
        }
    if galeno_creado:
        if not dry_run:
            db.add_all(galenos_os.values())
            await db.flush()
        else:
            for nv, g in galenos_os.items():
                g.id = -nv  # transitorio: sólo para calcular el precio de la vista previa
    usados = {c.nivel if c.nivel is not None else 1 for c, _ in codigos}
    faltan = sorted(usados - set(galenos_os))
    if faltan:
        raise HTTPException(409, {
            "mensaje": f"{os_nombre} no tiene vigente el {g_nombre} en "
                       f"{'el nivel' if len(faltan) == 1 else 'los niveles'} "
                       f"{', '.join(map(str, faltan))}. Cargalo en Galenos y volvé a aplicar.",
            "faltan": faltan, "requiere_galeno": not galenos_os,
        })
    gasto = (await db.execute(select(Galeno).where(
        Galeno.obra_social_nro == obra_social_nro, Galeno.codigo == (n.galeno_gastos_codigo or GASTO_QUIRURGICO),
        Galeno.activo == True, Galeno.nivel.is_(None),
    ))).scalars().first()
    galenos_por_id = {g.id: g for g in galenos_os.values()}
    if gasto is not None:
        galenos_por_id[gasto.id] = gasto

    # 2. Estado de cada código en la O.S. Sólo cuenta el precio NE, que es el que
    # crea este nomenclador: un NN no lo reemplaza (el lookup prefiere NE) ni choca
    # con él (la variante es otra). Con NE, la vigencia se carga igual.
    nom_ids = [nom.id for _, nom in codigos]
    cods = [nom.codigo for _, nom in codigos]
    # Vigencias NE por variante (nomenclador_id, especialidad), de la más vieja a la
    # más nueva.
    ne: dict[tuple[int, Optional[int]], list[Valor]] = {}
    for v in (await db.execute(select(Valor).where(
        Valor.obra_social_nro == obra_social_nro, Valor.nomenclador_id.in_(nom_ids),
        Valor.origen == "NE",
    ).order_by(Valor.vigencia_desde))).scalars() if nom_ids else []:
        ne.setdefault((v.nomenclador_id, v.especialidad_id_colegio), []).append(v)
    pares = {
        p.nomenclador_id: p for p in (await db.execute(select(CodigoObraSocial).where(
            CodigoObraSocial.obra_social_nro == obra_social_nro,
            CodigoObraSocial.nomenclador_id.in_(nom_ids),
        ))).scalars()
    } if nom_ids else {}

    def _clasificar(c, nom) -> Optional[tuple[str, str]]:
        if not nom.activo:
            return "omitido", "El código está inactivo en el catálogo"
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
        if par is not None and par.sin_restriccion_especialidad:
            return True, []
        if ya:
            return False, ya
        # Sin alta (sólo en la vista previa) lo que haría `dar_de_alta`. Con alta pero sin nada
        # configurado (p. ej. la que dejó un precio NN, o una alta anterior sin precio), se hereda
        # del catálogo igual que en el alta: "sin restricción" si el código lo es (se marca en el
        # par al aplicar) y, si no, la plantilla (se habilita al aplicar).
        if nom.sin_restriccion_especialidad:
            return True, []
        return False, sorted(plantillas.get(nom.codigo, []))

    # 5. Precios.
    filas_out: list[AplicarNiveladoFila] = []
    a_crear: list[tuple[dict, list[dict]]] = []
    a_habilitar: list[dict] = []
    a_sin_restriccion: list[str] = []  # pares con alta que heredan "sin restricción" del catálogo
    a_liberar: list[tuple[int, Optional[int]]] = []  # (nomenclador_id, especialidad)
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
        # "Sin restricción" + nomenclador con lista de especialidades: el código sigue sin restricción
        # (el NN vale para cualquier médico), pero el NE de este nomenclador es sólo de sus
        # especialidades: un precio NE por cada una. El lookup prefiere el NE de la especialidad del
        # médico y, para las demás, cae al NN.
        con_lista = sin_restr and bool(del_nomenclador)
        if not sin_restr and not esps:
            filas_out.append(AplicarNiveladoFila(
                **base, estado="sin_quien_factura",
                motivo="Se da de alta sin precio: no tiene especialidades que lo facturen",
            ))
            continue
        # Quién factura es lo configurado en la O.S. (`esps`); el nomenclador sólo acota en cuáles
        # de ellas se carga precio. Las demás no se tocan, tengan o no precio.
        if con_lista:
            aplican = sorted(del_nomenclador)
        else:
            aplican = esps if sin_restr or not del_nomenclador else [e for e in esps if e in del_nomenclador]
        if not sin_restr and not aplican:
            nombres = dict((await db.execute(
                select(Especialidad.ID_COLEGIO_ESPE, Especialidad.ESPECIALIDAD)
                .where(Especialidad.ID_COLEGIO_ESPE.in_(esps))
            )).all())
            filas_out.append(AplicarNiveladoFila(
                **base, estado="fuera_de_especialidad",
                motivo="Ninguna de sus especialidades en esta obra social es de este nomenclador: "
                       + ", ".join(str(nombres.get(e) or e).strip() for e in esps),
            ))
            continue
        comps, galeno = motor.componentes_de_codigo(nom, c.nivel, c.unidades, escala, galenos_os, gasto)
        par = pares.get(nom.id)
        if con_lista:
            # Toda NE con especialidad la habilita en la O.S.
            a_habilitar.extend(
                {"obra_social_nro": obra_social_nro, "codigo": nom.codigo, "especialidad_id_colegio": e}
                for e in aplican if e not in habilitadas.get(nom.codigo, [])
            )
        elif par is not None and not sin_restr and not habilitadas.get(nom.codigo):
            a_habilitar.extend(
                {"obra_social_nro": obra_social_nro, "codigo": nom.codigo, "especialidad_id_colegio": e}
                for e in esps
            )
        if par is not None and sin_restr and not par.sin_restriccion_especialidad:
            a_sin_restriccion.append(nom.codigo)
        categoria = par.categoria if par else None
        variantes = aplican if con_lista or not sin_restr else [None]
        for esp in variantes:
            a_crear.append(({
                "obra_social_nro": obra_social_nro, "nomenclador_id": nom.id, "origen": "NE",
                "codigo": nom.codigo,
                "descripcion": (par.descripcion if par and par.descripcion else nom.descripcion),
                "nivel": galeno.nivel, "complejidad": par.complejidad if par else None,
                "categoria": categoria,
                "requiere_autorizacion": par.requiere_autorizacion if par else None,
                "especialidad_id_colegio": esp, "sin_restriccion_especialidad": sin_restr,
                "por_presupuesto": False, "nomenclador_nivelado_id": n.id,
                "cantidad_ayudantes": _forzar_ayudantes_honorarios_individuales(
                    categoria, nom, par.cantidad_ayudantes if par else None,
                ),
                # Si ya tenía precio, conserva el coseguro de la última vigencia.
                "coseguro": ne[(nom.id, esp)][-1].coseguro if (nom.id, esp) in ne else Decimal("0"),
                "vigencia_desde": vigencia_desde, "observacion": None,
            }, comps))
        if any((nom.id, esp) in ne for esp in variantes):
            borradas = sum(
                1 for esp in variantes for v in ne.get((nom.id, esp), [])
                if v.vigencia_desde >= vigencia_desde
            )
            a_liberar.extend((nom.id, esp) for esp in variantes)
            filas_out.append(AplicarNiveladoFila(
                **base, estado="reemplazar" if dry_run else "reemplazado",
                precios=len(variantes), precio=_precio(comps, galenos_por_id),
                vigencias_borradas=borradas,
                motivo=(
                    f"Ya tenía precio NE: se borran {borradas} vigencia(s) desde el "
                    f"{vigencia_desde.strftime('%d/%m/%Y')} en adelante" if borradas
                    else "Ya tenía precio NE: se cierra y se abre la vigencia nueva"
                ),
            ))
            continue
        filas_out.append(AplicarNiveladoFila(
            **base, estado="crear" if dry_run else "creado",
            precios=len(variantes), precio=_precio(comps, galenos_por_id),
        ))

    if a_habilitar and not dry_run:
        await db.execute(insert(ValorEspecialidad), a_habilitar)
    if not dry_run:
        for codigo in a_sin_restriccion:
            await service.fijar_sin_restriccion_par(db, obra_social_nro, codigo, True)
    if cambio is not None:
        # Todo valor del nomenclador anterior se elimina; los códigos que también están en este
        # se vuelven a crear con las unidades del nuevo (ya están en `a_liberar`).
        propios = set(a_liberar)
        a_liberar.extend(k for k in viejo_usados if k not in propios)
        cambio.valores_a_crear = len(a_crear)
        cambio.codigos_sin_reemplazo = len({k[0] for k in viejo_usados} - {v["nomenclador_id"] for v, _ in a_crear})
    if a_liberar and not dry_run:
        await service.liberar_vigencias(db, obra_social_nro, "NE", a_liberar, vigencia_desde)
    if a_crear and not dry_run:
        await service.persistir_valores_en_bloque(db, a_crear, motivo="carga_inicial")

    def cuenta(estado: str) -> int:
        return sum(1 for f in filas_out if f.estado == estado)

    return AplicarNiveladoOut(
        dry_run=dry_run, obra_social_nro=obra_social_nro, nomenclador=n.nombre,
        galeno_nombre=g_nombre or g_codigo, galeno_creado=galeno_creado, cambio=cambio,
        resumen=AplicarNiveladoResumen(
            total=len(filas_out), crear=cuenta("crear") + cuenta("creado"),
            reemplazar=cuenta("reemplazar") + cuenta("reemplazado"),
            vigencias_borradas=sum(f.vigencias_borradas for f in filas_out),
            sin_quien_factura=cuenta("sin_quien_factura"),
            fuera_de_especialidad=cuenta("fuera_de_especialidad"),
            suspendido=cuenta("suspendido"), omitido=cuenta("omitido"),
            precios=len(a_crear), altas=len(altas),
        ),
        filas=filas_out,
    )
