"""Importar valores fijos NE de una obra social desde un Excel `CODIGO | DESCRIPCION | VALOR`.

Carga PRECIOS (no recalcula). Dos pasos sobre el mismo body:
- `clasificar`: sólo lectura. Dice, fila por fila, en qué estado está el código en la
  O.S. (sin catálogo, sin alta, precio vigente, vigencias posteriores…), qué acciones
  admite y cuál se sugiere.
- `aplicar`: vuelve a clasificar (no confía en la vista previa), valida las decisiones
  del usuario y escribe todo en una sola transacción — o se carga todo o nada.

Cada fila se carga como NE de modalidad fija: Honorarios = VALOR, Gastos y Ayudante
en 0 ("PRESUPUESTO" → por presupuesto, los tres en 0). Una variante por cada
especialidad que factura el código en la O.S., o una sola sin especialidad si el par
es "sin restricción" (mismo criterio que `routes_valores.importar_valores_csv`).

El Excel lo lee el front; acá llega como JSON y se vuelve a validar.
"""
from __future__ import annotations

import datetime
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.catalogs import ObrasSociales
from app.db.models.nomenclador_cmc import (
    CodigoObraSocial,
    HistorialPrecioCodigo,
    NomencladorCMC,
    NomencladorPlantillaEspecialidad,
    Valor,
    ValorEspecialidad,
)
from app.modules.nomenclador import alta_os, aplicar_plantilla, service
from app.modules.nomenclador.schemas import (
    AltaCodigoItem,
    AltaCodigosIn,
    DecisionFijaIn,
    FilaPreviaOut,
    ImportarFijosAplicarIn,
    ImportarFijosAplicarOut,
    ImportarFijosFilaIn,
    ImportarFijosIn,
    ImportarFijosPreviewOut,
    NomencladorCreate,
    ReplicaFilaOut,
    ReplicaOmitidaOut,
    ReplicaOSOut,
    VarianteActualOut,
)

ENCABEZADO = ("CODIGO", "DESCRIPCION", "VALOR")
PRESUPUESTO = "PRESUPUESTO"
_DOS_DEC = Decimal("0.01")

# Acciones que crean precio, por estado (además de `omitir`, que vale siempre).
_ACCIONES: dict[str, list[str]] = {
    "error": [],
    "sin_catalogo": ["crear_y_cargar"],
    "suspendido": ["reactivar_y_cargar"],
    "sin_alta": ["alta_y_cargar"],
    "sin_quien_factura": ["cargar"],
    "vigente_posterior": ["reemplazar"],
    "misma_vigencia": ["sobrescribir"],
    "rotar": ["rotar"],
    "nuevo": ["cargar"],
}

_MOTIVOS = {
    "sin_catalogo": "El código no existe en el catálogo del Colegio",
    "suspendido": "El código está suspendido en esta obra social",
    "sin_alta": "El código no está dado de alta en esta obra social",
    "sin_quien_factura": "Está dado de alta pero ninguna especialidad lo factura",
    "vigente_posterior": "Ya tiene precio NE con vigencia posterior a la que se carga",
    "misma_vigencia": "Ya tiene precio NE desde esta misma fecha",
    "rotar": "Tiene precio NE vigente: se cierra el día anterior y se abre el nuevo",
    "nuevo": "No tiene precio NE: se carga",
}


# ─── Parseo ───────────────────────────────────────────────────────────────────

def _norm(texto) -> str:
    """Mayúsculas, sin tildes y sin espacios sobrantes (para comparar encabezados)."""
    s = unicodedata.normalize("NFKD", str(texto or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.upper().split())


def validar_encabezado(encabezado: list) -> None:
    """422 si la fila 1 no es exactamente CODIGO | DESCRIPCION | VALOR (sin importar
    mayúsculas ni tildes). Celdas vacías a la derecha se toleran."""
    recibido = [_norm(c) for c in encabezado]
    while recibido and not recibido[-1]:
        recibido.pop()
    if tuple(recibido) != ENCABEZADO:
        raise HTTPException(422, (
            f"El archivo no tiene el formato esperado. La primera fila tiene que ser "
            f"{' | '.join(ENCABEZADO)} y llegó: {' | '.join(recibido) or '(vacía)'}."
        ))


def parsear_valor(raw) -> tuple[Optional[Decimal], bool, Optional[str]]:
    """`(importe, por_presupuesto, error)`. Acepta números, texto en formato AR
    ("$ 1.234.567", "1.234,50") y PRESUPUESTO."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, False, "Falta el valor"
    if isinstance(raw, bool):
        return None, False, f"Valor inválido: {raw}"
    if isinstance(raw, (int, float)):
        try:
            importe = Decimal(str(raw))
        except InvalidOperation:
            return None, False, f"Valor inválido: {raw}"
    else:
        s = str(raw)
        if _norm(s) == PRESUPUESTO:
            return None, True, None
        s = s.replace("$", "").replace(" ", "").replace(" ", "")
        if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", s) or re.fullmatch(r"\d+,\d+", s):
            s = s.replace(".", "").replace(",", ".")      # 1.234.567,50 → 1234567.50
        elif not re.fullmatch(r"\d+(\.\d{1,2})?", s):     # 37965 / 37965.5
            return None, False, f"Valor inválido: «{raw}»"
        importe = Decimal(s)
    if importe <= 0:
        return None, False, f"El valor tiene que ser mayor a 0 (llegó {raw})"
    return importe.quantize(_DOS_DEC), False, None


def normalizar_codigo(raw) -> str:
    if raw is None:
        return ""
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    return str(raw).strip()


def componentes_fijos(importe: Optional[Decimal], por_presupuesto: bool) -> list[dict]:
    """Honorarios = importe; Gastos y Ayudante en 0. Por presupuesto, los tres en 0."""
    honorarios = Decimal("0") if por_presupuesto or importe is None else importe
    return [
        {"concepto": concepto, "galeno_id": None, "cantidad": Decimal("0"),
         "valor_unitario": monto, "orden": orden}
        for orden, (concepto, monto) in enumerate((
            ("Honorarios", honorarios), ("Gastos", Decimal("0")), ("Ayudante", Decimal("0")),
        ))
    ]


# ─── Contexto de la O.S. (todo en lote) ───────────────────────────────────────

@dataclass
class _Contexto:
    noms: dict[str, NomencladorCMC]                       # código → catálogo (activo o no)
    pares: dict[int, CodigoObraSocial]                    # nomenclador_id → par
    habilitadas: dict[str, list[int]]                     # código → especialidades en la O.S.
    plantillas: dict[str, list[int]]                      # código → plantilla del Colegio
    ne: dict[tuple[int, Optional[int]], list[Valor]]      # variante → valores NE (viejo→nuevo)
    precios: dict[int, Decimal] = field(default_factory=dict)  # valor_id → precio_total


async def _contexto(db: AsyncSession, obra_social_nro: int, codigos: set[str]) -> _Contexto:
    noms = {
        n.codigo: n for n in (await db.execute(
            select(NomencladorCMC).where(NomencladorCMC.codigo.in_(codigos))
        )).scalars()
    } if codigos else {}
    nom_ids = [n.id for n in noms.values()]
    cods = list(noms)
    pares = {
        p.nomenclador_id: p for p in (await db.execute(select(CodigoObraSocial).where(
            CodigoObraSocial.obra_social_nro == obra_social_nro,
            CodigoObraSocial.nomenclador_id.in_(nom_ids),
        ))).scalars()
    } if nom_ids else {}
    habilitadas: dict[str, list[int]] = {}
    plantillas: dict[str, list[int]] = {}
    if cods:
        for cod, esp in (await db.execute(select(
            ValorEspecialidad.codigo, ValorEspecialidad.especialidad_id_colegio,
        ).where(
            ValorEspecialidad.obra_social_nro == obra_social_nro, ValorEspecialidad.codigo.in_(cods),
        ))).all():
            habilitadas.setdefault(cod, []).append(esp)
        for cod, esp in (await db.execute(select(
            NomencladorPlantillaEspecialidad.codigo, NomencladorPlantillaEspecialidad.especialidad_id_colegio,
        ).where(NomencladorPlantillaEspecialidad.codigo.in_(cods)))).all():
            plantillas.setdefault(cod, []).append(esp)
    ne: dict[tuple[int, Optional[int]], list[Valor]] = {}
    if nom_ids:
        for v in (await db.execute(select(Valor).where(
            Valor.obra_social_nro == obra_social_nro, Valor.nomenclador_id.in_(nom_ids),
            Valor.origen == "NE",
        ).order_by(Valor.vigencia_desde))).scalars():
            ne.setdefault((v.nomenclador_id, v.especialidad_id_colegio), []).append(v)
    ctx = _Contexto(noms, pares, habilitadas, plantillas, ne)
    valor_ids = [v.id for vs in ne.values() for v in vs]
    for i in range(0, len(valor_ids), 1000):
        for vid, precio in (await db.execute(select(
            HistorialPrecioCodigo.valores_id, HistorialPrecioCodigo.precio_total,
        ).where(HistorialPrecioCodigo.valores_id.in_(valor_ids[i:i + 1000])))).all():
            ctx.precios[vid] = precio
    return ctx


def _quien_factura(ctx: _Contexto, nom: NomencladorCMC) -> tuple[bool, list[int], bool]:
    """`(sin_restriccion, especialidades, es_sugerencia)`. Con alta: lo configurado en la
    O.S. Sin alta (o con alta pero nadie habilitado): la plantilla del Colegio, que es
    lo que usaría `dar_de_alta` — sólo una sugerencia."""
    par = ctx.pares.get(nom.id)
    if par is not None and par.sin_restriccion_especialidad:
        return True, [], False
    ya = sorted(ctx.habilitadas.get(nom.codigo, []))
    if ya:
        return False, ya, False
    if par is None and nom.sin_restriccion_especialidad:
        return True, [], True
    return False, sorted(ctx.plantillas.get(nom.codigo, [])), True


def _variacion(actual: Optional[Decimal], nuevo: Optional[Decimal]) -> Optional[float]:
    if actual is None or nuevo is None or actual == 0:
        return None
    return float(round((nuevo - actual) / actual * 100, 1))


# ─── Clasificación ────────────────────────────────────────────────────────────

async def clasificar(
    db: AsyncSession, obra_social_nro: int, vigencia: datetime.date, filas: list[ImportarFijosFilaIn],
) -> list[FilaPreviaOut]:
    """Estado, acciones posibles y acción sugerida de cada fila. No escribe."""
    # 1. Parseo + candidatos de código (tal cual y, si no existe, con ceros a la izquierda).
    parseadas = []
    candidatos: set[str] = set()
    for f in filas:
        cod = normalizar_codigo(f.codigo)
        importe, presupuesto, error = parsear_valor(f.valor)
        if not cod:
            error = "Falta el código"
        parseadas.append((f, cod, importe, presupuesto, error))
        if cod:
            candidatos.add(cod)
            if cod.isdigit() and len(cod) < 6:
                candidatos.add(cod.zfill(6))
    ctx = await _contexto(db, obra_social_nro, candidatos)
    nombres = await service.nombres_de_especialidades(
        db, {e for vs in (*ctx.habilitadas.values(), *ctx.plantillas.values()) for e in vs}
        | {esp for (_, esp) in ctx.ne if esp is not None}
    )

    out: list[FilaPreviaOut] = []
    for f, cod, importe, presupuesto, error in parseadas:
        avisos: list[str] = []
        if cod and cod not in ctx.noms and cod.isdigit() and len(cod) < 6 and cod.zfill(6) in ctx.noms:
            avisos.append(f"El código {cod} no existe; se tomó {cod.zfill(6)}")
            cod = cod.zfill(6)
        base = dict(
            fila=f.fila, codigo=cod, codigo_excel=normalizar_codigo(f.codigo) or None,
            descripcion_excel=(f.descripcion or "").strip() or None,
            valor=importe, por_presupuesto=presupuesto, avisos=avisos,
        )
        nom = ctx.noms.get(cod)
        if error is None and nom is not None and not nom.activo:
            error = "El código está inactivo en el catálogo del Colegio"
        if error is not None:
            out.append(FilaPreviaOut(**base, estado="error", motivo=error, acciones=["omitir"],
                                     accion_sugerida="omitir"))
            continue
        if nom is None:
            out.append(FilaPreviaOut(
                **base, estado="sin_catalogo", motivo=_MOTIVOS["sin_catalogo"],
                acciones=["crear_y_cargar", "omitir"], accion_sugerida="omitir",
                requiere_quien_factura=True,
            ))
            continue

        par = ctx.pares.get(nom.id)
        sin_restr, esps, sugerencia = _quien_factura(ctx, nom)
        variantes_dest = [None] if sin_restr else esps
        base.update(
            nomenclador_id=nom.id, categoria=(par.categoria if par and par.categoria else nom.categoria),
            descripcion_os=(par.descripcion if par and par.descripcion else nom.descripcion),
            especialidades=esps, sin_restriccion=sin_restr,
        )

        # Precio NE de cada variante destino.
        variantes: list[VarianteActualOut] = []
        hay_posterior = hay_misma = hay_cubre = False
        precios_previos: list[Decimal] = []
        for esp in variantes_dest:
            vs = ctx.ne.get((nom.id, esp), [])
            posteriores = [v for v in vs if v.vigencia_desde > vigencia]
            misma = [v for v in vs if v.vigencia_desde == vigencia]
            cubre = [v for v in vs if v.vigencia_desde < vigencia
                     and (v.vigencia_hasta is None or v.vigencia_hasta >= vigencia)]
            hay_posterior |= bool(posteriores)
            hay_misma |= bool(misma)
            hay_cubre |= bool(cubre)
            ref = (misma or cubre or [None])[-1]
            precio = ctx.precios.get(ref.id) if ref is not None else None
            if ref is not None and ref.por_presupuesto:
                precio = None
            if precio is not None:
                precios_previos.append(precio)
            variantes.append(VarianteActualOut(
                especialidad_id_colegio=esp,
                especialidad=nombres.get(esp) if esp is not None else None,
                precio_actual=precio,
                vigencia_desde=ref.vigencia_desde if ref is not None else None,
                vigencia_hasta=ref.vigencia_hasta if ref is not None else None,
                variacion_pct=_variacion(precio, importe),
                posteriores=len(posteriores) + len(misma),
            ))
        base["variantes"] = variantes
        if base["categoria"] == "Honorarios individuales":
            avisos.append("Honorarios individuales: el precio admite 2 ayudantes")

        if par is not None and par.estado == "suspendido":
            estado = "suspendido"
        elif par is None:
            estado = "sin_alta"
        elif not sin_restr and sugerencia:
            estado = "sin_quien_factura"
        elif hay_posterior:
            estado = "vigente_posterior"
        elif hay_misma:
            estado = "misma_vigencia"
        elif hay_cubre:
            estado = "rotar"
        else:
            estado = "nuevo"

        requiere_qf = estado == "sin_quien_factura" or (
            estado in ("sin_alta", "suspendido") and not sin_restr and not esps
        )
        # Mismo precio en todas las variantes (y mismo tipo): no hay nada que rotar.
        sin_cambio = (
            estado in ("rotar", "misma_vigencia") and not presupuesto
            and len(precios_previos) == len(variantes_dest) > 0
            and all(p == importe for p in precios_previos)
        ) or (
            estado in ("rotar", "misma_vigencia") and presupuesto and all(
                (ctx.ne.get((nom.id, e)) or [None])[-1] is not None
                and ctx.ne[(nom.id, e)][-1].por_presupuesto for e in variantes_dest
            )
        )
        if sin_cambio:
            avisos.append("El precio vigente ya es el mismo")
        if estado in ("sin_alta", "suspendido") and (hay_posterior or hay_misma):
            avisos.append("Tiene precios NE desde esta fecha o posteriores: se reemplazan")

        if estado == "suspendido":
            sugerida = "omitir"
        elif estado == "sin_alta":
            sugerida = None if requiere_qf else "alta_y_cargar"
        elif estado == "sin_quien_factura":
            sugerida = None
        elif estado == "vigente_posterior":
            sugerida = "omitir"
        elif sin_cambio:
            sugerida = "omitir"
        else:
            sugerida = _ACCIONES[estado][0]

        out.append(FilaPreviaOut(
            **base, estado=estado, motivo=_MOTIVOS[estado],
            acciones=[*_ACCIONES[estado], "omitir"], accion_sugerida=sugerida,
            requiere_quien_factura=requiere_qf, sin_cambio=sin_cambio,
        ))

    # 2. Códigos repetidos en el archivo: el usuario elige cuál usar (a lo sumo una).
    por_codigo: dict[str, list[FilaPreviaOut]] = {}
    for f in out:
        if f.estado != "error":
            por_codigo.setdefault(f.codigo, []).append(f)
    for repetidas in por_codigo.values():
        if len(repetidas) < 2:
            continue
        filas_txt = ", ".join(str(r.fila) for r in repetidas)
        for r in repetidas:
            r.estado_base = r.estado
            r.estado = "duplicado"
            r.motivo = f"El código aparece {len(repetidas)} veces en el archivo (filas {filas_txt}): elegí una"
            r.accion_sugerida = None
    return out


def _resumen(filas: list[FilaPreviaOut]) -> dict[str, int]:
    cuenta: dict[str, int] = {}
    for f in filas:
        cuenta[f.estado] = cuenta.get(f.estado, 0) + 1
    return cuenta


async def _validar_os(db: AsyncSession, obra_social_nro: int) -> None:
    existe = (await db.execute(select(ObrasSociales.ID).where(
        ObrasSociales.NRO_OBRASOCIAL == obra_social_nro
    ))).first()
    if existe is None:
        raise HTTPException(404, f"La obra social {obra_social_nro} no existe")


# ─── Réplica en la familia de la O.S. ─────────────────────────────────────────

def accion_en_destino(
    estado: str, accion_origen: str, sin_cambio: bool,
) -> tuple[Optional[str], Optional[str]]:
    """Qué se hace en una O.S. de la familia con una fila que se carga en la principal.

    Lo que el usuario no vio no se pisa: una vigencia igual o posterior en el destino
    sólo se reemplaza si en la principal también eligió reemplazar/sobrescribir, y un
    código suspendido ahí no se reactiva. Quién factura se copia de la principal."""
    if estado in ("sin_catalogo", "sin_alta"):
        return "alta_y_cargar", "Se da de alta con las mismas especialidades"
    if estado == "suspendido":
        return None, "Está suspendido en esta obra social"
    if estado == "sin_quien_factura":
        return "cargar", "Se habilitan las mismas especialidades"
    if estado == "vigente_posterior":
        if accion_origen == "reemplazar":
            return "reemplazar", "Se borran sus vigencias posteriores"
        return None, "Tiene una vigencia posterior"
    if estado == "misma_vigencia":
        if accion_origen in ("sobrescribir", "reemplazar"):
            return "sobrescribir", None
        return None, "Ya tiene ese precio" if sin_cambio else "Ya tiene precio desde esa fecha"
    if sin_cambio:
        return None, "Ya tiene ese precio"
    if estado == "error":
        return None, "No se pudo interpretar la fila"
    return ("rotar" if estado == "rotar" else "cargar"), None


def _accion_de_carga(f: FilaPreviaOut) -> Optional[str]:
    """La única acción que carga precio para la fila (las demás son `omitir`)."""
    return next((a for a in f.acciones if a != "omitir"), None)


async def _clasificar_destino(
    db: AsyncSession, nro: int, vigencia: datetime.date, filas_in: list[ImportarFijosFilaIn],
) -> dict[int, FilaPreviaOut]:
    """Clasificación de las filas en una O.S. de la familia, sin el estado `duplicado`
    (cuál de las repetidas se carga se decide en la principal)."""
    out = {}
    for f in await clasificar(db, nro, vigencia, filas_in):
        if f.estado == "duplicado":
            f.estado = f.estado_base
        out[f.fila] = f
    return out


async def previsualizar(db: AsyncSession, body: ImportarFijosIn) -> ImportarFijosPreviewOut:
    from app.modules.nomenclador.replicar_familia import familia_de

    validar_encabezado(body.encabezado)
    await _validar_os(db, body.obra_social_nro)
    filas = await clasificar(db, body.obra_social_nro, body.vigencia_desde, body.filas)
    familia = await familia_de(db, body.obra_social_nro)
    for os_fam in familia:
        destino = await _clasificar_destino(db, os_fam.nro_obra_social, body.vigencia_desde, body.filas)
        for f in filas:
            accion_origen = _accion_de_carga(f)
            d = destino.get(f.fila)
            if accion_origen is None or d is None:
                continue
            accion, motivo = accion_en_destino(d.estado, accion_origen, d.sin_cambio)
            f.replicas.append(ReplicaFilaOut(
                obra_social_nro=os_fam.nro_obra_social, estado=d.estado, accion=accion, motivo=motivo,
            ))
    return ImportarFijosPreviewOut(
        obra_social_nro=body.obra_social_nro, vigencia_desde=body.vigencia_desde, familia=familia,
        total=len(filas), por_estado=_resumen(filas), filas=filas,
    )


# ─── Aplicación ───────────────────────────────────────────────────────────────

def _validar_decisiones(
    filas: list[FilaPreviaOut], decisiones: list[DecisionFijaIn],
) -> dict[int, tuple[FilaPreviaOut, DecisionFijaIn]]:
    """Fila → (clasificación actual, decisión). 409 si algo cambió desde la vista
    previa; 422 si una decisión no vale para la fila."""
    por_fila = {d.fila: d for d in decisiones}
    if len(por_fila) != len(decisiones):
        raise HTTPException(422, "Hay decisiones repetidas para la misma fila")
    cambiadas = [f.fila for f in filas if f.fila in por_fila and por_fila[f.fila].estado_visto != f.estado]
    if cambiadas:
        raise HTTPException(409, (
            "Los datos cambiaron desde la vista previa (filas "
            f"{', '.join(map(str, cambiadas[:20]))}{'…' if len(cambiadas) > 20 else ''}). "
            "Volvé a previsualizar."
        ))
    errores: list[str] = []
    elegidas: dict[int, tuple[FilaPreviaOut, DecisionFijaIn]] = {}
    usados: dict[str, int] = {}
    for f in filas:
        d = por_fila.get(f.fila)
        if d is None:
            if f.accion_sugerida is None:
                errores.append(f"Fila {f.fila}: falta decidir qué hacer")
                continue
            d = DecisionFijaIn(fila=f.fila, estado_visto=f.estado, accion=f.accion_sugerida)
        if d.accion not in f.acciones:
            errores.append(f"Fila {f.fila}: la acción «{d.accion}» no vale para una fila en estado «{f.estado}»")
            continue
        if d.accion == "omitir":
            continue
        if f.requiere_quien_factura and not d.sin_restriccion and not d.especialidades:
            errores.append(f"Fila {f.fila}: elegí qué especialidades lo facturan o «sin restricción»")
            continue
        if f.codigo in usados:
            errores.append(f"Fila {f.fila}: el código {f.codigo} ya se carga en la fila {usados[f.codigo]}")
            continue
        usados[f.codigo] = f.fila
        elegidas[f.fila] = (f, d)
    if errores:
        raise HTTPException(422, {"mensaje": "Hay decisiones que no se pueden aplicar", "errores": errores})
    return elegidas


@dataclass
class _Carga:
    """Una fila a cargar en una O.S.: su clasificación ahí, la acción y quién factura."""
    fila: FilaPreviaOut
    accion: str
    especialidades: Optional[list[int]] = None
    sin_restriccion: Optional[bool] = None


@dataclass
class _Totales:
    precios: int = 0
    filas: int = 0
    rotadas: int = 0
    altas: int = 0
    borradas: int = 0


async def _aplicar_en_os(
    db: AsyncSession, os_nro: int, vigencia: datetime.date, cargas: list[_Carga], usuario: Optional[str],
) -> _Totales:
    """Altas, quién factura y precios de `cargas` en una O.S. No hace commit. Los
    códigos nuevos ya tienen que estar en el catálogo."""
    from app.modules.nomenclador.routes_valores import _forzar_ayudantes_honorarios_individuales

    tot = _Totales(filas=len(cargas))
    ctx = await _contexto(db, os_nro, {c.fila.codigo for c in cargas})

    # 1. Altas / reactivaciones.
    altas = [c for c in cargas if c.accion in ("crear_y_cargar", "alta_y_cargar", "reactivar_y_cargar")]
    if altas:
        resultados = await alta_os.dar_de_alta(db, AltaCodigosIn(items=[
            AltaCodigoItem(
                obra_social_nro=os_nro, nomenclador_id=ctx.noms[c.fila.codigo].id,
                descripcion=c.fila.descripcion_excel,
                especialidades=(None if c.sin_restriccion or not c.especialidades else c.especialidades),
                sin_restriccion_especialidad=True if c.sin_restriccion else None,
            ) for c in altas
        ]), usuario)
        fallidas = [r for r in resultados if r.estado == "error"]
        if fallidas:
            raise HTTPException(422, {"mensaje": "No se pudo dar de alta", "errores": [
                f"Código {r.codigo}: {r.motivo}" for r in fallidas
            ]})
        tot.altas = len(altas)

    # 2. Quién factura, para los que estaban dados de alta sin nadie que los facture.
    for c in cargas:
        if c.fila.estado != "sin_quien_factura" and c.fila.estado_base != "sin_quien_factura":
            continue
        try:
            if c.sin_restriccion:
                await service.fijar_sin_restriccion_par(db, os_nro, c.fila.codigo, True)
            elif c.especialidades:
                await service.reemplazar_especialidades(db, os_nro, c.fila.codigo, c.especialidades)
        except ValueError as e:
            raise HTTPException(422, f"Fila {c.fila.fila}: {e}")
    await db.flush()

    # 3. Precios: con lo que quedó configurado, cada fila va a sus variantes.
    ctx = await _contexto(db, os_nro, {c.fila.codigo for c in cargas})
    a_liberar: list[tuple[int, Optional[int]]] = []
    con_previo: list[tuple[dict, list[dict]]] = []
    sin_previo: list[tuple[dict, list[dict]]] = []
    for c in cargas:
        f = c.fila
        nom = ctx.noms[f.codigo]
        par = ctx.pares[nom.id]
        # Lo que quedó configurado en la O.S. (no la plantilla: ya se aplicó en el alta).
        sin_restr = bool(par.sin_restriccion_especialidad)
        variantes = [None] if sin_restr else sorted(ctx.habilitadas.get(nom.codigo, []))
        if not variantes:
            raise HTTPException(422, f"Fila {f.fila}: el código {f.codigo} quedó sin especialidades que lo facturen")
        habia_precio = False
        for esp in variantes:
            previos = ctx.ne.get((nom.id, esp), [])
            anteriores = [v for v in previos if v.vigencia_desde < vigencia]
            tot.borradas += sum(1 for v in previos if v.vigencia_desde >= vigencia)
            if previos:
                habia_precio = True
                a_liberar.append((nom.id, esp))
            ultimo = anteriores[-1] if anteriores else (previos[-1] if previos else None)
            categoria = par.categoria
            campos = {
                "obra_social_nro": os_nro, "nomenclador_id": nom.id, "origen": "NE",
                "codigo": nom.codigo,
                "descripcion": par.descripcion or nom.descripcion,
                "nivel": None, "complejidad": par.complejidad, "categoria": categoria,
                "requiere_autorizacion": par.requiere_autorizacion,
                "especialidad_id_colegio": esp, "sin_restriccion_especialidad": sin_restr,
                "por_presupuesto": f.por_presupuesto,
                "cantidad_ayudantes": _forzar_ayudantes_honorarios_individuales(
                    categoria, nom,
                    ultimo.cantidad_ayudantes if ultimo is not None else par.cantidad_ayudantes,
                ),
                "coseguro": ultimo.coseguro if ultimo is not None else Decimal("0"),
                "vigencia_desde": vigencia, "observacion": None,
            }
            (con_previo if previos else sin_previo).append(
                (campos, componentes_fijos(f.valor, f.por_presupuesto))
            )
        if habia_precio:
            tot.rotadas += 1

    if a_liberar:
        await service.liberar_vigencias(db, os_nro, "NE", a_liberar, vigencia)
    if con_previo:
        await service.persistir_valores_en_bloque(db, con_previo, motivo="valor_fijo_actualizado")
    if sin_previo:
        await service.persistir_valores_en_bloque(db, sin_previo, motivo="carga_inicial")
    tot.precios = len(con_previo) + len(sin_previo)
    return tot


async def _replicar_en(
    db: AsyncSession, origen: int, nro: int, nombre: str, vigencia: datetime.date,
    filas_in: list[ImportarFijosFilaIn], elegidas: dict[int, tuple[FilaPreviaOut, DecisionFijaIn]],
    usuario: Optional[str],
) -> ReplicaOSOut:
    """Carga en una O.S. de la familia lo que se cargó en la principal, en su propio
    SAVEPOINT: si falla, esa O.S. queda sin cambios y las demás siguen."""
    out = ReplicaOSOut(obra_social_nro=nro, nombre=nombre, estado="ok")
    destino = await _clasificar_destino(
        db, nro, vigencia, [fi for fi in filas_in if fi.fila in elegidas],
    )
    # Quién factura en la principal, tal como quedó después de cargarla.
    ctx_origen = await _contexto(db, origen, {f.codigo for f, _ in elegidas.values()})
    cargas: list[_Carga] = []
    for nro_fila, (f, d) in elegidas.items():
        dest = destino[nro_fila]
        accion, motivo = accion_en_destino(dest.estado, d.accion, dest.sin_cambio)
        if accion is None:
            out.omitidas.append(ReplicaOmitidaOut(fila=nro_fila, codigo=f.codigo, motivo=motivo or "No se replica"))
            continue
        nom = ctx_origen.noms[f.codigo]
        sin_restr = bool(ctx_origen.pares[nom.id].sin_restriccion_especialidad)
        cargas.append(_Carga(
            fila=dest, accion=accion, sin_restriccion=sin_restr or None,
            especialidades=None if sin_restr else sorted(ctx_origen.habilitadas.get(nom.codigo, [])),
        ))
    if not cargas:
        return out
    try:
        async with db.begin_nested():
            tot = await _aplicar_en_os(db, nro, vigencia, cargas, usuario)
    except HTTPException as e:
        detalle = e.detail if isinstance(e.detail, str) else "; ".join(e.detail.get("errores", []))
        return ReplicaOSOut(obra_social_nro=nro, nombre=nombre, estado="error",
                            motivo=detalle or "No se pudo cargar", omitidas=out.omitidas)
    except Exception as e:  # noqa: BLE001 — se reporta por O.S., no corta las demás
        return ReplicaOSOut(obra_social_nro=nro, nombre=nombre, estado="error", motivo=str(e),
                            omitidas=out.omitidas)
    out.precios_creados, out.filas_cargadas, out.altas = tot.precios, tot.filas, tot.altas
    return out


async def aplicar(db: AsyncSession, body: ImportarFijosAplicarIn, usuario: Optional[str]) -> ImportarFijosAplicarOut:
    """Escribe lo decidido en la O.S. y, si se pidió, en las de su familia. La
    principal es todo o nada; cada O.S. de la familia va aparte (ver `_replicar_en`).
    No hace commit: lo hace la ruta (o rollback si algo falla)."""
    from app.modules.nomenclador.replicar_familia import _destinos_validos

    validar_encabezado(body.encabezado)
    await _validar_os(db, body.obra_social_nro)
    os_nro, vigencia = body.obra_social_nro, body.vigencia_desde
    destinos = await _destinos_validos(db, os_nro, body.replicar_en) if body.replicar_en else {}
    filas = await clasificar(db, os_nro, vigencia, body.filas)
    elegidas = _validar_decisiones(filas, body.decisiones)

    # 1. Códigos nuevos en el catálogo (son del Colegio: valen para toda la familia).
    creados = 0
    for f, d in elegidas.values():
        if d.accion != "crear_y_cargar":
            continue
        try:
            await aplicar_plantilla.crear_codigo(db, NomencladorCreate(
                codigo=f.codigo, descripcion=f.descripcion_excel,
                categoria=(d.categoria or "").strip() or None,
                sin_restriccion_especialidad=bool(d.sin_restriccion),
                especialidades=[] if d.sin_restriccion else (d.especialidades or []),
            ))
        except (aplicar_plantilla.CodigoExistente, ValueError) as e:
            raise HTTPException(422, f"Fila {f.fila}: {e}")
        creados += 1

    # 2. La O.S. principal.
    tot = await _aplicar_en_os(db, os_nro, vigencia, [
        _Carga(fila=f, accion=d.accion, especialidades=d.especialidades, sin_restriccion=d.sin_restriccion)
        for f, d in elegidas.values()
    ], usuario)

    # 3. Las de la familia.
    replicas = [
        await _replicar_en(db, os_nro, nro, nombre, vigencia, body.filas, elegidas, usuario)
        for nro, nombre in destinos.items()
    ] if elegidas else []

    return ImportarFijosAplicarOut(
        obra_social_nro=os_nro, vigencia_desde=vigencia,
        precios_creados=tot.precios, filas_cargadas=tot.filas, filas_rotadas=tot.rotadas,
        altas=tot.altas, codigos_creados=creados, vigencias_borradas=tot.borradas,
        omitidas=len(filas) - len(elegidas), replicas=replicas,
    )
