"""
Motor de generación y regeneración de historial_precio_codigo.

Todas las funciones públicas corren DENTRO de la transacción del caller.
Si cualquier paso falla, el rollback se propaga al caller automáticamente.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import and_, delete, exists, func, insert, or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.money import quantize_money
from app.db.models.nomenclador_cmc import (
    CodigoObraSocial,
    Galeno,
    HistorialPrecioCodigo,
    MedicoCodigoHabilitado,
    NomencladorCMC,
    NomencladorDescripcionLegacy,
    NomencladorPlantillaEspecialidad,
    Valor,
    ValorComponente,
    ValorEspecialidad,
)
from app.db.models.medico import ListadoMedico
from app.db.models.catalogs import Especialidad
from app.modules.nomenclador import service_vias
from app.modules.nomenclador.schemas import (
    ComponenteLookupOut,
    LookupPrecioOut,
)


# ─────────────────────────────────────────────────────────────────────────────
# Origen / prioridad (fuente de verdad de la prioridad — vive en código, no en DB)
# ─────────────────────────────────────────────────────────────────────────────
#
# Índice 0 = máxima prioridad. Para sumar un origen: agregarlo al enum
# schemas.Origen y a esta tupla en la posición que corresponda. Sin migración.
ORIGEN_PRIORIDAD: tuple[str, ...] = ("NE", "NN")

# Rank para orígenes desconocidos: pierden contra cualquier origen conocido (fail-safe).
_PRIORIDAD_DESCONOCIDA = len(ORIGEN_PRIORIDAD)


def prioridad_origen(origen: str) -> int:
    """Menor = mayor prioridad. Origen desconocido → último (fail-safe)."""
    try:
        return ORIGEN_PRIORIDAD.index(origen)
    except ValueError:
        return _PRIORIDAD_DESCONOCIDA


# ─────────────────────────────────────────────────────────────────────────────
# Resolución del código y especialidades habilitadas (por obra social)
# ─────────────────────────────────────────────────────────────────────────────
#
# Fase 2 de la reestructura del nomenclador: `nm_nomenclador` pasó a ser SOLO el
# catálogo del Colegio (código + categoría + complejidad), sin ownership por OS —
# `resolver_nomenclador`/`filtro_pertenencia` quedan acá por compatibilidad de firma
# con sus muchos llamadores, pero ya no hacen precedencia. Las especialidades
# habilitadas (antes en `nm_nomenclador_especialidad`, con precedencia OS-propia >
# Colegio) viven ahora en `nm_valor_especialidad`, una fila por (obra_social_nro,
# codigo, especialidad) sin ambigüedad que resolver.

async def resolver_nomenclador(
    db: AsyncSession, codigo: str, obra_social_nro: Optional[int] = None
) -> Optional[NomencladorCMC]:
    """Código → fila del catálogo. `codigo` es identidad única (UNIQUE en DB desde
    la fase 3 de la reestructura del nomenclador); `obra_social_nro` se mantiene en
    la firma sin usarse para no tener que tocar cada uno de sus llamadores. Devuelve
    None si el código no existe."""
    stmt = select(NomencladorCMC).where(
        NomencladorCMC.codigo == codigo, NomencladorCMC.activo.is_(True)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def filtro_pertenencia(obra_social_nro: Optional[int] = None):
    """Antes acotaba el catálogo a lo propio de una OS + lo compartido. El catálogo ya
    no distingue por OS (ver `resolver_nomenclador`), así que esto no filtra nada —
    se mantiene la firma por compatibilidad con sus llamadores."""
    return true()


async def especialidades_habilitadas_de(
    db: AsyncSession, codigo: str, obra_social_nro: int
) -> set[int]:
    """Especialidades que pueden facturar un código en una obra social puntual.

    Lee directo de `nm_valor_especialidad`: una fila por (obra_social_nro, codigo,
    especialidad), sin precedencia que resolver — reemplaza a `nm_nomenclador_
    especialidad`, que colgaba del código y necesitaba desempatar OS-propia vs.
    compartida del Colegio.
    """
    stmt = select(ValorEspecialidad.especialidad_id_colegio).where(
        ValorEspecialidad.obra_social_nro == obra_social_nro,
        ValorEspecialidad.codigo == codigo,
    )
    return {row for row in (await db.execute(stmt)).scalars()}


async def par_sin_restriccion(db: AsyncSession, codigo: str, obra_social_nro: int) -> bool:
    """¿El par (obra_social_nro, codigo) es "sin restricción de especialidad"?

    Es dato del PAR: lo decide el alta del código en la O.S. (`nm_codigo_obra_social`,
    etapa 3). Sólo si el par no tiene alta (datos previos a la etapa 3) cae al
    criterio viejo: True si alguna fila ACTIVA de `nm_valores` lo tiene.
    """
    flag_par = (await db.execute(select(CodigoObraSocial.sin_restriccion_especialidad).where(
        CodigoObraSocial.codigo == codigo,
        CodigoObraSocial.obra_social_nro == obra_social_nro,
    ).limit(1))).scalar_one_or_none()
    if flag_par is not None:
        return bool(flag_par)
    return bool((await db.execute(
        select(exists().where(
            Valor.codigo == codigo,
            Valor.obra_social_nro == obra_social_nro,
            Valor.estado == "activo",
            Valor.sin_restriccion_especialidad == True,
        ))
    )).scalar())


async def _par_de(
    db: AsyncSession, obra_social_nro: Optional[int], nomenclador_id: int
) -> Optional[CodigoObraSocial]:
    """El alta del código en la O.S. (etapa 3), si existe."""
    if obra_social_nro is None:
        return None
    return (await db.execute(select(CodigoObraSocial).where(
        CodigoObraSocial.obra_social_nro == obra_social_nro,
        CodigoObraSocial.nomenclador_id == nomenclador_id,
    ))).scalar_one_or_none()


async def _tiene_valor_activo(
    db: AsyncSession, obra_social_nro: Optional[int], nomenclador_id: int
) -> bool:
    if obra_social_nro is None:
        return False
    return (await db.execute(select(Valor.id).where(
        Valor.obra_social_nro == obra_social_nro,
        Valor.nomenclador_id == nomenclador_id,
        Valor.estado == "activo",
    ).limit(1))).scalar_one_or_none() is not None


async def fijar_sin_restriccion_par(
    db: AsyncSession, obra_social_nro: int, codigo: str, valor: bool,
    *, excluir_id: Optional[int] = None,
) -> None:
    """Deja `sin_restriccion_especialidad = valor` en TODAS las filas activas del par
    (es dato del par, ver `variantes_del_par`).

    Pasarlo a False se rechaza si el par tiene una variante NE activa SIN
    especialidad: esa fila sólo es válida mientras el par es sin restricción (ver
    `schemas.validar_reglas_origen`) — quedaría huérfana, sin especialidad con la
    que cotizar. Hay que cerrarla primero.
    """
    if not valor:
        stmt = select(Valor.id).where(
            Valor.obra_social_nro == obra_social_nro,
            Valor.codigo == codigo,
            Valor.origen == "NE",
            Valor.especialidad_id_colegio.is_(None),
            Valor.estado == "activo",
        )
        if excluir_id is not None:
            stmt = stmt.where(Valor.id != excluir_id)
        huerfana = (await db.execute(stmt.limit(1))).scalar_one_or_none()
        if huerfana is not None:
            raise ValueError(
                f"No se puede quitar 'sin restricción de especialidad': el valor NE "
                f"{huerfana} no tiene especialidad y sólo es válido mientras el código "
                "sea sin restricción. Ciérrelo primero o cárguelo por especialidad."
            )
    for v in await variantes_del_par(db, obra_social_nro, codigo, excluir_id=excluir_id):
        v.sin_restriccion_especialidad = valor
    # El alta del código en la O.S. (etapa 3) es quien manda (ver par_sin_restriccion).
    await db.execute(
        update(CodigoObraSocial)
        .where(CodigoObraSocial.obra_social_nro == obra_social_nro, CodigoObraSocial.codigo == codigo)
        .values(sin_restriccion_especialidad=valor)
    )
    await db.flush()


async def validar_especialidad_habilitada(
    db: AsyncSession, codigo: str, obra_social_nro: int, especialidad_id_colegio: int
) -> None:
    """Toda variante NE implica que su especialidad puede facturar el código: crear un
    Valor NE para una especialidad la habilita automáticamente en (obra_social_nro,
    codigo) si todavía no lo estaba — idempotente. Antes esto exigía cargar la
    habilitación primero por separado; ahora las especialidades se administran
    enteramente desde el modal de Valores (`ValorUpdate.especialidades`), así que
    darla de alta acá evita un paso manual redundante al crear la primera variante.
    """
    from app.db.models.catalogs import Especialidad  # import local: evita ciclo con catalogs

    existe = (await db.execute(
        select(Especialidad.ID_COLEGIO_ESPE).where(
            Especialidad.ID_COLEGIO_ESPE == especialidad_id_colegio
        )
    )).scalar_one_or_none()
    if existe is None:
        raise ValueError(f"La especialidad {especialidad_id_colegio} no existe en el catálogo")

    ya_existe = (await db.execute(
        select(ValorEspecialidad.id).where(
            ValorEspecialidad.obra_social_nro == obra_social_nro,
            ValorEspecialidad.codigo == codigo,
            ValorEspecialidad.especialidad_id_colegio == especialidad_id_colegio,
        )
    )).scalar_one_or_none()
    if ya_existe is None:
        db.add(ValorEspecialidad(
            obra_social_nro=obra_social_nro, codigo=codigo,
            especialidad_id_colegio=especialidad_id_colegio,
        ))
        await db.flush()


async def reemplazar_especialidades(
    db: AsyncSession, obra_social_nro: int, codigo: str, especialidad_ids: list[int],
) -> None:
    """Reemplaza POR COMPLETO las especialidades habilitadas de (obra_social_nro,
    codigo) por la lista dada (no se suma a lo existente). Es el único lugar del
    sistema donde se editan — ver `routes_valores.update_valor_metadata`.

    Rechaza sacar una especialidad que todavía tiene un Valor NE activo dependiendo
    de ella (quedaría huérfana) — mismo chequeo que existía en el CRUD de
    `nm_nomenclador_especialidad` antes de la reestructura.
    """
    from app.db.models.catalogs import Especialidad

    especialidad_ids = list(dict.fromkeys(especialidad_ids))  # sin duplicados, preserva orden
    if especialidad_ids:
        validas = set((await db.execute(
            select(Especialidad.ID_COLEGIO_ESPE).where(
                Especialidad.ID_COLEGIO_ESPE.in_(especialidad_ids)
            )
        )).scalars())
        invalidas = set(especialidad_ids) - validas
        if invalidas:
            raise ValueError(f"Especialidad(es) inexistente(s): {sorted(invalidas)}")

    activas_ne = set((await db.execute(
        select(Valor.especialidad_id_colegio).where(
            Valor.obra_social_nro == obra_social_nro,
            Valor.codigo == codigo,
            Valor.origen == "NE",
            Valor.estado == "activo",
        )
    )).scalars())
    faltantes = activas_ne - set(especialidad_ids)
    if faltantes:
        raise ValueError(
            f"No se puede quitar la especialidad {sorted(faltantes)}: tiene un valor "
            "NE activo que depende de ella. Ciérrelo primero."
        )

    await db.execute(delete(ValorEspecialidad).where(
        ValorEspecialidad.obra_social_nro == obra_social_nro,
        ValorEspecialidad.codigo == codigo,
    ))
    for eid in especialidad_ids:
        db.add(ValorEspecialidad(
            obra_social_nro=obra_social_nro, codigo=codigo, especialidad_id_colegio=eid,
        ))
    await db.flush()


async def variantes_del_par(
    db: AsyncSession, obra_social_nro: int, codigo: str, *, excluir_id: Optional[int] = None,
) -> list[Valor]:
    """Todas las filas de Valor ACTIVAS de (obra_social_nro, codigo) — cualquier
    origen (NE + NN), a diferencia de `variantes_hermanas` que solo mira NE. Se usa
    para propagar datos que son del PAR y no de la variante (descripcion,
    sin_restriccion_especialidad) a todas sus filas — ver update_valor_metadata."""
    stmt = select(Valor).where(
        Valor.obra_social_nro == obra_social_nro,
        Valor.codigo == codigo,
        Valor.estado == "activo",
    )
    if excluir_id is not None:
        stmt = stmt.where(Valor.id != excluir_id)
    return list((await db.execute(stmt)).scalars())


async def variantes_hermanas(db: AsyncSession, valor: Valor) -> list[Valor]:
    """NE activas del mismo (obra_social_nro, nomenclador_id) que `valor`, excluyéndolo:
    las variantes por especialidad de un mismo código+OS, para propagar una edición a
    todas de una vez (ver ValorCerrarYCrearIn.aplicar_a_variantes)."""
    if valor.origen != "NE":
        return []
    stmt = select(Valor).where(
        Valor.obra_social_nro == valor.obra_social_nro,
        Valor.nomenclador_id == valor.nomenclador_id,
        Valor.origen == "NE",
        Valor.estado == "activo",
        Valor.id != valor.id,
    )
    return list((await db.execute(stmt)).scalars())


async def descripciones_legacy(
    db: AsyncSession, pares: set[tuple[str, Optional[int]]],
) -> dict[tuple[str, Optional[int]], str]:
    """Fallback de última instancia para (código, obra_social_nro) que no tienen
    ningún `Valor` con descripción propia: snapshot congelado del catálogo del
    Colegio al momento de la fase 1 de la reestructura del nomenclador (ver
    `NomencladorDescripcionLegacy`). Se elimina recién cuando esos pares tengan
    descripción por otro medio — ver plan de migración, obstáculo 2.

    Misma precedencia que tenía el catálogo antes de la reestructura: propia de
    la OS > compartida del Colegio. Devuelve un dict clavado por los MISMOS
    `(codigo, obra_social_nro)` que se pidieron (aunque el match haya salido de
    la fila compartida), para que el caller pueda indexar directo con `.get(par)`.
    """
    codigos = {c for c, _ in pares}
    if not codigos:
        return {}
    filas = (await db.execute(
        select(NomencladorDescripcionLegacy).where(NomencladorDescripcionLegacy.codigo.in_(codigos))
    )).scalars().all()
    propias: dict[tuple[str, int], str] = {}
    compartidas: dict[str, str] = {}
    for f in filas:
        if f.obra_social_nro is None:
            compartidas[f.codigo] = f.descripcion
        else:
            propias[(f.codigo, f.obra_social_nro)] = f.descripcion

    out: dict[tuple[str, Optional[int]], str] = {}
    for codigo, os_nro in pares:
        if os_nro is not None and (codigo, os_nro) in propias:
            out[(codigo, os_nro)] = propias[(codigo, os_nro)]
        elif codigo in compartidas:
            out[(codigo, os_nro)] = compartidas[codigo]
    return out


def descripcion_efectiva(valor: Optional[Valor], legacy: Optional[str] = None) -> str:
    """Cómo se nombra el código en el contexto de una OS.

    `Valor.descripcion` si la variante la tiene cargada (siempre, desde la fase 2
    de la reestructura del nomenclador); si no, `legacy` — el fallback congelado
    del catálogo del Colegio para las filas viejas que nunca llegaron a tener la
    propia (ver `descripciones_legacy`). `""` si ninguna existe.
    """
    if valor is not None and valor.descripcion and valor.descripcion.strip():
        return valor.descripcion
    return legacy or ""


def categoria_efectiva(
    valor: Optional[Valor], nomenclador: Optional[NomencladorCMC]
) -> Optional[str]:
    """Categoría del código para una OS: override del valor > la del catálogo.

    Decide el `tipo` de la prestación y si los gastos se fuerzan a 0 bajo sanatorio
    (ver facturacion/service.py::derivar_tipo y _gasto_forzado_a_cero) — por eso el
    override por OS: la misma práctica puede ser 'Practica' para una obra social y
    'Honorarios individuales' para otra.
    """
    if valor is not None and valor.categoria and valor.categoria.strip():
        return valor.categoria
    return nomenclador.categoria if nomenclador is not None else None


def requiere_autorizacion_efectiva(valor: Optional[Valor]) -> bool:
    """¿La práctica necesita autorización previa de la obra social?

    Dato 100% de `Valor` — el catálogo del Colegio ya no opina (ver plan de
    reestructura del nomenclador). `None` en el valor (nadie cargó nada) = False.

    No confundir con `ObraManual.requiere_autorizacion` (validaciones), que es un
    todo-o-nada por obra social. Los dos niveles se combinan con OR en el gate de carga.
    """
    return bool(valor.requiere_autorizacion) if valor is not None else False


# ─────────────────────────────────────────────────────────────────────────────
# Cálculo de precio
# ─────────────────────────────────────────────────────────────────────────────

async def calcular_precio_total(
    valor_id: int, db: AsyncSession
) -> tuple[Decimal, list]:
    """
    Suma los 3 componentes de un Valor (Honorarios/Gastos/Ayudante) y devuelve
    (precio_total, componentes_snapshot). No existen componentes opcionales: todos
    suman al precio_total.
    """
    stmt = (
        select(ValorComponente)
        .where(ValorComponente.valor_id == valor_id, ValorComponente.activo == True)
        .order_by(ValorComponente.orden)
    )
    result = await db.execute(stmt)
    componentes = result.scalars().all()

    galenos = {
        gid: await db.get(Galeno, gid)
        for gid in {c.galeno_id for c in componentes if c.galeno_id is not None}
    }
    return precio_y_snapshot(componentes, galenos)


def precio_y_snapshot(componentes, galenos: dict) -> tuple[Decimal, list]:
    """Parte pura de `calcular_precio_total`: suma los componentes (ya ordenados
    por `orden`) con los galenos dados por id. La usa también la aplicación en
    bloque de `aplicar_plantilla`, que ya tiene los componentes y galenos en mano."""
    precio_total = Decimal("0")
    snapshot = []

    for comp in componentes:
        if comp.galeno_id is not None:
            # calculable
            galeno = galenos.get(comp.galeno_id)
            precio_unidad = galeno.valor_unitario if galeno else Decimal("0")
            subtotal = quantize_money(comp.cantidad * precio_unidad)
            snapshot.append({
                "componente_id": comp.id,
                "concepto": comp.concepto,
                "tipo": "calculable",
                "galeno_id": comp.galeno_id,
                "galeno_codigo": galeno.codigo if galeno else None,
                "galeno_nivel": galeno.nivel if galeno else None,
                "cantidad": str(comp.cantidad),
                "valor_unitario": str(precio_unidad),
                "subtotal": str(subtotal),
            })
        else:
            # fijo
            subtotal = quantize_money(comp.valor_unitario or Decimal("0"))
            snapshot.append({
                "componente_id": comp.id,
                "concepto": comp.concepto,
                "tipo": "fijo",
                "galeno_id": None,
                "galeno_codigo": None,
                "galeno_nivel": None,
                "cantidad": str(comp.cantidad),
                "valor_unitario": str(subtotal),
                "subtotal": str(subtotal),
            })

        precio_total += subtotal

    return quantize_money(precio_total), snapshot


# ─────────────────────────────────────────────────────────────────────────────
# Motor de historial — Regla B: nuevo valores (valor fijo o estructura)
# ─────────────────────────────────────────────────────────────────────────────

def _cond_variante(origen: str, especialidad_id: Optional[int]):
    """Condición de pertenencia a una variante del historial.

    La variante es (origen, especialidad_id_colegio); NULL en especialidad = sin perfil.
    """
    cond = HistorialPrecioCodigo.origen == origen
    if especialidad_id is None:
        return and_(cond, HistorialPrecioCodigo.especialidad_id_colegio.is_(None))
    return and_(cond, HistorialPrecioCodigo.especialidad_id_colegio == especialidad_id)


async def regenerar_historial_por_valores(
    nuevo_valor_id: int,
    fecha_corte: Optional[datetime.date],
    db: AsyncSession,
    motivo: str = "carga_inicial",
    nueva_vigencia_desde: Optional[datetime.date] = None,
) -> None:
    """
    Regla B: cierra la fila vigente del historial para la misma variante
    (nomenclador_id, obra_social_nro, especialidad_id_colegio) e inserta una nueva.

    Si ya existe una fila con la misma vigencia_desde (ej: actualización de galeno
    en la misma fecha que la vigencia del valor), actualiza esa fila en lugar de
    intentar insertar un duplicado.
    """
    nuevo_valor = await db.get(Valor, nuevo_valor_id)
    if not nuevo_valor:
        return

    precio_total, snapshot = await calcular_precio_total(nuevo_valor_id, db)
    vigencia_nueva = nueva_vigencia_desde or nuevo_valor.vigencia_desde
    variante = nuevo_valor.especialidad_id_colegio
    origen = nuevo_valor.origen

    # Si ya existe una fila para esa vigencia_desde, actualizarla en lugar de close+insert
    result = await db.execute(
        select(HistorialPrecioCodigo).where(
            HistorialPrecioCodigo.nomenclador_id == nuevo_valor.nomenclador_id,
            HistorialPrecioCodigo.obra_social_nro == nuevo_valor.obra_social_nro,
            _cond_variante(origen, variante),
            HistorialPrecioCodigo.vigencia_desde == vigencia_nueva,
        )
    )
    fila_existente = result.scalar_one_or_none()

    if fila_existente:
        fila_existente.precio_total = precio_total
        fila_existente.componentes_snapshot = snapshot
        fila_existente.motivo_cambio = motivo
        fila_existente.vigencia_hasta = None
        fila_existente.valores_id = nuevo_valor_id
        fila_existente.fecha_cambio = datetime.datetime.utcnow()
        await db.flush()
        return

    # Cerrar fila vigente anterior de la misma variante (si existe y hay fecha de corte)
    if fecha_corte:
        await db.execute(
            update(HistorialPrecioCodigo)
            .where(
                HistorialPrecioCodigo.nomenclador_id == nuevo_valor.nomenclador_id,
                HistorialPrecioCodigo.obra_social_nro == nuevo_valor.obra_social_nro,
                _cond_variante(origen, variante),
                HistorialPrecioCodigo.vigencia_hasta.is_(None),
            )
            .values(vigencia_hasta=fecha_corte)
        )

    historial = HistorialPrecioCodigo(
        nomenclador_id=nuevo_valor.nomenclador_id,
        obra_social_nro=nuevo_valor.obra_social_nro,
        origen=origen,
        especialidad_id_colegio=variante,
        vigencia_desde=vigencia_nueva,
        vigencia_hasta=None,
        precio_total=precio_total,
        valores_id=nuevo_valor_id,
        componentes_snapshot=snapshot,
        motivo_cambio=motivo,
        referencia_cambio_id=nuevo_valor_id,
        fecha_cambio=datetime.datetime.utcnow(),
    )
    db.add(historial)
    await db.flush()


async def persistir_valor(
    db: AsyncSession,
    valor: Valor,
    componentes: list[dict],
    *,
    motivo: str,
    fecha_corte: Optional[datetime.date],
) -> Valor:
    """Punto único de alta de un `Valor`: lo persiste junto con sus componentes
    **y su fila de historial**, en la misma transacción.

    El precio vive en dos tablas — `nm_valores` (lo que se edita y se ve en el
    panel) y `nm_historial_precio_codigo` (lo único contra lo que cotiza
    `lookup_precio`) — y el esquema no las ata: la FK va en la dirección
    contraria a la que haría falta, así que un valor activo sin historial es un
    estado perfectamente legal para la base, y silencioso. Cuando hay una fila
    NN vigente para el mismo código, ésta gana por descarte y la prestación se
    factura al precio nacional sin que nadie se entere.

    Antes el alta y su historial eran dos pasos que cada endpoint encadenaba por
    su cuenta, en una decena de lugares: alcanzaba con que un camino nuevo se
    olvidara del segundo. Pasó de verdad — los 520 valores NE de Prevención
    Salud (OS 103) quedaron sin historial desde junio de 2026 y se detectó tres
    meses después, mirando un honorario a ojo. Por eso las dos escrituras se
    hacen acá adentro y no se delegan al llamador; `tests/test_valor_historial_
    atomico.py` falla el build si alguien vuelve a construir un `Valor` fuera de
    este camino, y `valores_activos_sin_historial` vigila el dato.

    `componentes` son dicts listos para `ValorComponente(valor_id=..., **datos)`.
    Para el alta desde el panel, con validación de galenos y relleno de los tres
    conceptos, ver `routes_valores._crear_valor_con_componentes`, que termina
    llamando igual al motor de historial.
    """
    db.add(valor)
    await db.flush()
    from app.modules.nomenclador import alta_os  # import local: alta_os importa este módulo
    await alta_os.asegurar_pares(db, [alta_os.campos_de_valor(valor)])
    for datos in componentes:
        db.add(ValorComponente(valor_id=valor.id, **datos))
    await db.flush()
    await regenerar_historial_por_valores(valor.id, fecha_corte, db, motivo=motivo)
    return valor


async def persistir_valores_en_bloque(
    db: AsyncSession,
    filas: list[tuple[dict, list[dict]]],
    *,
    motivo: str,
) -> dict[tuple, int]:
    """Versión EN BLOQUE de `persistir_valor`: mismo contrato (valor + componentes +
    fila de historial, en la misma transacción), con una cantidad fija de consultas
    en vez de ~10 por valor. Para altas masivas (aplicar la plantilla de un código a
    muchas O.S., completar el nomenclador NN de una O.S.): hechas de a una tardaban
    minutos contra la base de prod.

    `filas`: `(campos_del_valor, componentes)`. `campos_del_valor` son las columnas
    de `nm_valores` (sin id); `componentes`, dicts listos para `ValorComponente`.

    Precondición: ninguna variante `(obra_social_nro, nomenclador_id, origen,
    especialidad_id_colegio)` de `filas` tiene hoy un Valor activo — es como se
    recuperan los ids (MySQL no tiene RETURNING). Por lo mismo no hay fecha de
    corte: no cierra historial previo (no hay valor previo activo que cerrar). Si ya
    existe una fila de historial con la misma vigencia_desde, se actualiza en vez
    de duplicarla (igual que `regenerar_historial_por_valores`).

    Devuelve `{(os, nomenclador_id, origen, especialidad_id_colegio): valor_id}`.
    No hace commit.
    """
    if not filas:
        return {}

    def _clave(v: dict) -> tuple:
        return (v["obra_social_nro"], v["nomenclador_id"], v["origen"],
                v.get("especialidad_id_colegio"))

    por_clave = {_clave(v): (v, comps) for v, comps in filas}
    if len(por_clave) != len(filas):
        raise ValueError("persistir_valores_en_bloque: variantes repetidas en el lote")

    # 0. El código queda dado de alta en cada O.S. (etapa 3) si todavía no lo estaba.
    from app.modules.nomenclador import alta_os  # import local: alta_os importa este módulo
    await alta_os.asegurar_pares(db, [v for v, _ in filas])

    # 1. Valores
    await db.execute(insert(Valor), [
        {"estado": "activo", "vigencia_hasta": None, **v} for v, _ in filas
    ])
    os_nros = {k[0] for k in por_clave}
    nom_ids = {k[1] for k in por_clave}
    ids: dict[tuple, int] = {}
    for vid, os_nro, nom_id, origen, esp in (await db.execute(
        select(Valor.id, Valor.obra_social_nro, Valor.nomenclador_id, Valor.origen,
               Valor.especialidad_id_colegio).where(
            Valor.obra_social_nro.in_(os_nros), Valor.nomenclador_id.in_(nom_ids),
            Valor.estado == "activo",
        )
    )).all():
        clave = (os_nro, nom_id, origen, esp)
        if clave in por_clave:
            if clave in ids:  # violaría la precondición: ya había un activo
                raise ValueError(f"persistir_valores_en_bloque: ya existía un activo para {clave}")
            ids[clave] = vid

    # 2. Componentes
    comps_rows = [
        {"valor_id": ids[clave], **c}
        for clave, (_, comps) in por_clave.items() for c in comps
    ]
    if comps_rows:
        await db.execute(insert(ValorComponente), comps_rows)
    comps_por_valor: dict[int, list] = {vid: [] for vid in ids.values()}
    for c in (await db.execute(
        select(
            ValorComponente.id, ValorComponente.valor_id, ValorComponente.concepto,
            ValorComponente.galeno_id, ValorComponente.cantidad, ValorComponente.valor_unitario,
        ).where(
            ValorComponente.valor_id.in_(list(ids.values())), ValorComponente.activo == True,
        ).order_by(ValorComponente.valor_id, ValorComponente.orden)
    )).all():
        comps_por_valor[c.valor_id].append(c)

    # 3. Historial
    galeno_ids = {c.galeno_id for cs in comps_por_valor.values() for c in cs if c.galeno_id}
    galenos = {
        g.id: g for g in (await db.execute(
            select(Galeno).where(Galeno.id.in_(galeno_ids))
        )).scalars()
    } if galeno_ids else {}
    previas: dict[tuple, HistorialPrecioCodigo] = {}
    for h in (await db.execute(
        select(HistorialPrecioCodigo).where(
            HistorialPrecioCodigo.obra_social_nro.in_(os_nros),
            HistorialPrecioCodigo.nomenclador_id.in_(nom_ids),
            HistorialPrecioCodigo.vigencia_desde.in_({v["vigencia_desde"] for v, _ in filas}),
        )
    )).scalars():
        clave = (h.obra_social_nro, h.nomenclador_id, h.origen, h.especialidad_id_colegio)
        if clave in por_clave and por_clave[clave][0]["vigencia_desde"] == h.vigencia_desde:
            previas[clave] = h

    ahora = datetime.datetime.utcnow()
    nuevas = []
    for clave, vid in ids.items():
        v = por_clave[clave][0]
        precio, snapshot = precio_y_snapshot(comps_por_valor[vid], galenos)
        previa = previas.get(clave)
        if previa is not None:
            previa.precio_total = precio
            previa.componentes_snapshot = snapshot
            previa.motivo_cambio = motivo
            previa.vigencia_hasta = None
            previa.valores_id = vid
            previa.fecha_cambio = ahora
            continue
        nuevas.append({
            "nomenclador_id": clave[1], "obra_social_nro": clave[0], "origen": clave[2],
            "especialidad_id_colegio": clave[3], "vigencia_desde": v["vigencia_desde"],
            "vigencia_hasta": None, "precio_total": precio, "valores_id": vid,
            "componentes_snapshot": snapshot, "motivo_cambio": motivo,
            "referencia_cambio_id": vid, "fecha_cambio": ahora,
        })
    if nuevas:
        await db.execute(insert(HistorialPrecioCodigo), nuevas)
    await db.flush()
    return ids


async def cerrar_historial_de_valor(
    valor_id: int,
    fecha_corte: datetime.date,
    db: AsyncSession,
) -> None:
    """Cierra la fila vigente del historial que apunta a un valor (al darlo de baja)."""
    await db.execute(
        update(HistorialPrecioCodigo)
        .where(
            HistorialPrecioCodigo.valores_id == valor_id,
            HistorialPrecioCodigo.vigencia_hasta.is_(None),
        )
        .values(vigencia_hasta=fecha_corte)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Chequeo de integridad valores ↔ historial
# ─────────────────────────────────────────────────────────────────────────────

async def valores_activos_sin_historial(
    db: AsyncSession,
    obra_social_nro: Optional[int] = None,
    limite_detalle: int = 200,
) -> dict:
    """
    Invariante del módulo: **todo `Valor` activo tiene al menos una fila en
    `nm_historial_precio_codigo`**. Devuelve los que lo violan.

    Por qué hace falta un chequeo explícito: el precio vive en dos tablas y nada
    en el esquema las ata. La FK va en la dirección contraria a la que haría falta
    (obliga a que todo historial apunte a un valor, no a que todo valor tenga
    historial), así que "valor activo sin historial" es un estado perfectamente
    legal para la base — y silencioso: el panel lee `nm_valores` y ve el precio,
    pero `lookup_precio` sólo mira el historial y cotiza como si no existiera.
    Cuando hay una fila NN vigente para el mismo código, ésta gana por descarte y
    la prestación se factura al precio nacional sin que nadie se entere.

    Pasó de verdad (2026-09): los 520 valores NE de Prevención Salud (OS 103)
    quedaron sin historial desde junio; 70 códigos cotizaron mal en silencio y
    115 quedaron sin precio. Se detectó a ojo, mirando un honorario.

    El total tiene que dar **0**. Cualquier otro número es un bug de datos.
    """
    cond_huerfano = ~exists(
        select(HistorialPrecioCodigo.id)
        .where(HistorialPrecioCodigo.valores_id == Valor.id)
        .correlate(Valor)
    )
    filtros = [Valor.estado == "activo", cond_huerfano]
    if obra_social_nro is not None:
        filtros.append(Valor.obra_social_nro == obra_social_nro)

    total = (await db.execute(
        select(func.count()).select_from(Valor).where(*filtros)
    )).scalar_one()

    if not total:
        return {"total": 0, "por_obra_social": [], "detalle": []}

    filas_os = (await db.execute(
        select(
            Valor.obra_social_nro,
            func.count().label("valores"),
            func.count(func.distinct(Valor.nomenclador_id)).label("codigos"),
            func.min(Valor.vigencia_desde).label("vigencia_min"),
            func.max(Valor.vigencia_desde).label("vigencia_max"),
        )
        .where(*filtros)
        .group_by(Valor.obra_social_nro)
        .order_by(func.count().desc())
    )).all()

    detalle = (await db.execute(
        select(
            Valor.id, Valor.obra_social_nro, Valor.nomenclador_id, Valor.codigo,
            Valor.origen, Valor.especialidad_id_colegio, Valor.vigencia_desde,
        )
        .where(*filtros)
        .order_by(Valor.obra_social_nro, Valor.codigo)
        .limit(limite_detalle)
    )).all()

    return {
        "total": total,
        "por_obra_social": [
            {
                "obra_social_nro": r.obra_social_nro,
                "valores": r.valores,
                "codigos": r.codigos,
                "vigencia_min": r.vigencia_min,
                "vigencia_max": r.vigencia_max,
            }
            for r in filas_os
        ],
        "detalle": [
            {
                "valor_id": r.id,
                "obra_social_nro": r.obra_social_nro,
                "nomenclador_id": r.nomenclador_id,
                "codigo": r.codigo,
                "origen": r.origen,
                "especialidad_id_colegio": r.especialidad_id_colegio,
                "vigencia_desde": r.vigencia_desde,
            }
            for r in detalle
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Motor de historial — Regla A: sube precio de un galeno
# ─────────────────────────────────────────────────────────────────────────────

async def regenerar_historial_por_galeno(
    galeno_id_anterior: int,
    nuevo_galeno_id: int,
    vigencia_desde: datetime.date,
    db: AsyncSession,
) -> int:
    """
    Regla A:
    1. Actualiza galeno_id en todos los componentes activos que apuntaban al galeno anterior.
    2. Para cada nomenclador_id único afectado, cierra la fila del historial y abre una nueva.
    Retorna la cantidad de códigos actualizados.
    """
    # 1. Identificar componentes activos apuntando al galeno anterior
    stmt = (
        select(ValorComponente)
        .join(Valor, Valor.id == ValorComponente.valor_id)
        .where(
            ValorComponente.galeno_id == galeno_id_anterior,
            ValorComponente.activo == True,
            Valor.estado == "activo",
        )
    )
    result = await db.execute(stmt)
    componentes = result.scalars().all()

    if not componentes:
        return 0

    # Colectar valor_ids únicos afectados
    valor_ids_afectados = list({c.valor_id for c in componentes})

    # 2. Actualizar galeno_id en todos esos componentes
    await db.execute(
        update(ValorComponente)
        .where(
            ValorComponente.galeno_id == galeno_id_anterior,
            ValorComponente.activo == True,
        )
        .values(galeno_id=nuevo_galeno_id)
    )

    # 3. Para cada valor afectado, regenerar historial
    fecha_corte = vigencia_desde - datetime.timedelta(days=1)
    for valor_id in valor_ids_afectados:
        await regenerar_historial_por_valores(
            valor_id, fecha_corte, db, motivo="galeno_actualizado",
            nueva_vigencia_desde=vigencia_desde,
        )
        # Reasignar la referencia de cambio al galeno nuevo
        await db.execute(
            update(HistorialPrecioCodigo)
            .where(
                HistorialPrecioCodigo.valores_id == valor_id,
                HistorialPrecioCodigo.vigencia_hasta.is_(None),
                HistorialPrecioCodigo.motivo_cambio == "galeno_actualizado",
            )
            .values(referencia_cambio_id=nuevo_galeno_id)
        )

    return len(valor_ids_afectados)


# ─────────────────────────────────────────────────────────────────────────────
# Regla A para unidades-plantilla del galeno + import de galenos entre OS
# ─────────────────────────────────────────────────────────────────────────────

# concepto del componente → columna de unidad-plantilla en Galeno / NomencladorCMC
_CONCEPTO_A_UNIDAD: dict[str, str] = {
    "Honorarios": "unidades_honorarios",
    "Ayudante": "unidades_ayudante",
    "Gastos": "unidades_gastos",
}


async def propagar_unidades_galeno(
    galeno: Galeno,
    conceptos: List[str],
    vigencia_desde: datetime.date,
    db: AsyncSession,
) -> int:
    """
    Opción A — propaga un cambio de unidades-plantilla del galeno a los valores vigentes.

    Pisa la `cantidad` de TODOS los componentes activos (de valores activos) que usan
    este galeno en los `conceptos` indicados, con la nueva unidad del galeno, y regenera
    el historial de los códigos afectados (motivo 'galeno_actualizado').
    Asume que `galeno.unidades_*` ya fueron actualizadas en la sesión. No comitea.
    Devuelve la cantidad de componentes pisados.
    """
    if not conceptos:
        return 0
    stmt = (
        select(ValorComponente)
        .join(Valor, Valor.id == ValorComponente.valor_id)
        .where(
            ValorComponente.galeno_id == galeno.id,
            ValorComponente.activo == True,
            Valor.estado == "activo",
            ValorComponente.concepto.in_(conceptos),
        )
    )
    componentes = (await db.execute(stmt)).scalars().all()
    if not componentes:
        return 0

    valor_ids: set[int] = set()
    for comp in componentes:
        comp.cantidad = getattr(galeno, _CONCEPTO_A_UNIDAD[comp.concepto])
        valor_ids.add(comp.valor_id)
    await db.flush()

    fecha_corte = vigencia_desde - datetime.timedelta(days=1)
    for valor_id in valor_ids:
        await regenerar_historial_por_valores(
            valor_id, fecha_corte, db, motivo="galeno_actualizado",
            nueva_vigencia_desde=vigencia_desde,
        )
        await db.execute(
            update(HistorialPrecioCodigo)
            .where(
                HistorialPrecioCodigo.valores_id == valor_id,
                HistorialPrecioCodigo.vigencia_hasta.is_(None),
                HistorialPrecioCodigo.motivo_cambio == "galeno_actualizado",
            )
            .values(referencia_cambio_id=galeno.id)
        )
    return len(componentes)


def _galenos_iguales(a: Galeno, b: Galeno, solo_valor: bool = False) -> bool:
    """
    True si dos galenos ya coinciden en lo que la importación va a copiar.
    Con `solo_valor` compara solo el precio (las unidades del destino se conservan);
    si no, compara precio + unidades-plantilla.
    """
    if a.valor_unitario != b.valor_unitario:
        return False
    if solo_valor:
        return True
    return (
        a.unidades_honorarios == b.unidades_honorarios
        and a.unidades_ayudante == b.unidades_ayudante
        and a.unidades_gastos == b.unidades_gastos
    )


async def _hay_mezcla_niveles(
    db: AsyncSession, obra_social_nro: int, codigo: str, nivel: Optional[int]
) -> bool:
    """True si crear (codigo, nivel) en la OS mezclaría galenos nivelados con sin-nivel."""
    stmt = select(Galeno.id).where(
        Galeno.obra_social_nro == obra_social_nro,
        Galeno.codigo == codigo,
        Galeno.vigencia_hasta.is_(None),
        Galeno.activo == True,
        Galeno.nivel.is_not(None) if nivel is None else Galeno.nivel.is_(None),
    ).limit(1)
    return (await db.execute(stmt)).first() is not None


async def _rotar_galeno_destino(
    db: AsyncSession,
    destino_g: Galeno,
    origen_g: Galeno,
    vigencia_desde: datetime.date,
    solo_valor: bool = False,
) -> Galeno:
    """
    Rota el galeno vigente del destino con los datos del origen: cierra la fila vigente,
    crea una nueva con el precio + unidades del origen, reapunta los componentes activos
    del destino al galeno nuevo (pisando la cantidad con la nueva unidad-plantilla donde
    exista) y regenera el historial (motivo 'replicacion'). No comitea.

    Con `solo_valor=True` copia SOLO el `valor_unitario` del origen y conserva las
    unidades-plantilla del destino (y no pisa la cantidad de los componentes): sirve
    para actualizar el "valor del galeno" sin tocar la estructura de niveles del destino.
    """
    fecha_corte = vigencia_desde - datetime.timedelta(days=1)
    if destino_g.vigencia_desde > fecha_corte:
        raise ValueError(
            f"vigencia_desde ({vigencia_desde}) debe ser posterior a la vigencia "
            f"actual del galeno destino ({destino_g.vigencia_desde})"
        )
    destino_g.vigencia_hasta = fecha_corte
    destino_g.activo = False
    await db.flush()

    fuente_unidades = destino_g if solo_valor else origen_g
    nuevo = Galeno(
        obra_social_nro=destino_g.obra_social_nro,
        codigo=destino_g.codigo,
        nombre=destino_g.nombre,
        nivel=destino_g.nivel,
        vigencia_desde=vigencia_desde,
        vigencia_hasta=None,
        valor_unitario=origen_g.valor_unitario,
        unidades_honorarios=fuente_unidades.unidades_honorarios,
        unidades_ayudante=fuente_unidades.unidades_ayudante,
        unidades_gastos=fuente_unidades.unidades_gastos,
        visible=destino_g.visible,
    )
    db.add(nuevo)
    await db.flush()

    comps = (await db.execute(
        select(ValorComponente)
        .join(Valor, Valor.id == ValorComponente.valor_id)
        .where(
            ValorComponente.galeno_id == destino_g.id,
            ValorComponente.activo == True,
            Valor.estado == "activo",
        )
    )).scalars().all()

    valor_ids: set[int] = set()
    for comp in comps:
        comp.galeno_id = nuevo.id
        # En modo solo_valor se conserva la cantidad del componente (la estructura
        # de niveles del destino no cambia; solo cambia el precio por unidad).
        if not solo_valor:
            nueva_unidad = getattr(nuevo, _CONCEPTO_A_UNIDAD[comp.concepto])
            if nueva_unidad is not None:
                comp.cantidad = nueva_unidad
        valor_ids.add(comp.valor_id)
    await db.flush()

    for valor_id in valor_ids:
        await regenerar_historial_por_valores(
            valor_id, fecha_corte, db, motivo="replicacion",
            nueva_vigencia_desde=vigencia_desde,
        )
        await db.execute(
            update(HistorialPrecioCodigo)
            .where(
                HistorialPrecioCodigo.valores_id == valor_id,
                HistorialPrecioCodigo.vigencia_hasta.is_(None),
                HistorialPrecioCodigo.motivo_cambio == "replicacion",
            )
            .values(referencia_cambio_id=nuevo.id)
        )
    return nuevo


async def _convertir_destino_a_nivelado(
    db: AsyncSession,
    destino_viejo: Galeno,
    origen_rows: list[Galeno],
    obra_social_nro_destino: int,
    vigencia_desde: datetime.date,
) -> None:
    """
    Reemplaza el galeno sin-nivel vigente del destino por el set nivelado del origen:
    cierra la fila sin nivel, crea una fila por nivel (precio + unidades del origen)
    y reapunta los componentes activos del destino al galeno del nivel de cada valor,
    regenerando historial (motivo 'replicacion'). Valida ANTES de mutar: todo valor
    activo que use el galeno debe tener un nivel presente en el set del origen.
    No comitea.
    """
    fecha_corte = vigencia_desde - datetime.timedelta(days=1)
    if destino_viejo.vigencia_desde > fecha_corte:
        raise ValueError(
            f"vigencia_desde ({vigencia_desde}) debe ser posterior a la vigencia "
            f"actual del galeno destino ({destino_viejo.vigencia_desde})"
        )

    pares = (await db.execute(
        select(ValorComponente, Valor.nivel)
        .join(Valor, Valor.id == ValorComponente.valor_id)
        .where(
            ValorComponente.galeno_id == destino_viejo.id,
            ValorComponente.activo == True,
            Valor.estado == "activo",
        )
    )).all()

    niveles_origen = {g.nivel for g in origen_rows}
    for comp, nivel_valor in pares:
        if nivel_valor not in niveles_origen:
            raise ValueError(
                f"no se puede convertir a nivelado: el valor {comp.valor_id} "
                f"(nivel {nivel_valor}) usa este galeno y el origen no tiene ese nivel"
            )

    destino_viejo.vigencia_hasta = fecha_corte
    destino_viejo.activo = False
    await db.flush()

    nuevos_por_nivel: dict[int, Galeno] = {}
    for g in origen_rows:
        nuevo = Galeno(
            obra_social_nro=obra_social_nro_destino,
            codigo=g.codigo,
            nombre=g.nombre,
            nivel=g.nivel,
            vigencia_desde=vigencia_desde,
            vigencia_hasta=None,
            valor_unitario=g.valor_unitario,
            unidades_honorarios=g.unidades_honorarios,
            unidades_ayudante=g.unidades_ayudante,
            unidades_gastos=g.unidades_gastos,
            visible=destino_viejo.visible,
        )
        db.add(nuevo)
        nuevos_por_nivel[g.nivel] = nuevo
    await db.flush()

    nivel_por_valor: dict[int, int] = {}
    for comp, nivel_valor in pares:
        nuevo = nuevos_por_nivel[nivel_valor]
        comp.galeno_id = nuevo.id
        nueva_unidad = getattr(nuevo, _CONCEPTO_A_UNIDAD[comp.concepto])
        if nueva_unidad is not None:
            comp.cantidad = nueva_unidad
        nivel_por_valor[comp.valor_id] = nivel_valor
    await db.flush()

    for valor_id, nivel_valor in nivel_por_valor.items():
        await regenerar_historial_por_valores(
            valor_id, fecha_corte, db, motivo="replicacion",
            nueva_vigencia_desde=vigencia_desde,
        )
        await db.execute(
            update(HistorialPrecioCodigo)
            .where(
                HistorialPrecioCodigo.valores_id == valor_id,
                HistorialPrecioCodigo.vigencia_hasta.is_(None),
                HistorialPrecioCodigo.motivo_cambio == "replicacion",
            )
            .values(referencia_cambio_id=nuevos_por_nivel[nivel_valor].id)
        )


async def importar_galenos_entre_os(
    obra_social_nro_origen: int,
    obra_social_nro_destino: int,
    vigencia_desde: datetime.date,
    db: AsyncSession,
    codigos: Optional[list[str]] = None,
    convertir_a_nivelado: bool = False,
    solo_valor: bool = False,
) -> dict:
    """
    Copia los galenos VIGENTES de la OS origen a la OS destino (precio + unidades):
    - `codigos` limita la importación a esos códigos (None/vacío = todos).
    - destino sin ese (codigo, nivel)         → crea el galeno.
    - destino con ese (codigo, nivel) vigente → rota vigencia (conserva historial) y
                                                 reapunta los valores del destino al nuevo.
    - destino idéntico (precio + unidades)    → no hace nada (sin_cambios).
    - origen nivelado y destino sin nivel     → error, salvo `convertir_a_nivelado`:
      reemplaza el sin-nivel del destino por los niveles del origen y reapunta los
      valores del destino al galeno del nivel de cada valor.
    Con `solo_valor` la rotación copia SOLO el `valor_unitario` del origen y conserva
    las unidades-plantilla del destino (solo aplica a galenos ya existentes en el destino;
    los que no existen se crean igual con los datos del origen, no hay unidades que conservar).
    Partial-success: los ítems con error se reportan y el resto se importa igual.
    No comitea: corre dentro de la transacción del caller.
    """
    stmt = select(Galeno).where(
        Galeno.obra_social_nro == obra_social_nro_origen,
        Galeno.vigencia_hasta.is_(None),
        Galeno.activo == True,
    )
    if codigos:
        stmt = stmt.where(Galeno.codigo.in_(codigos))
    origen_galenos = (await db.execute(
        stmt.order_by(Galeno.codigo, Galeno.nivel)
    )).scalars().all()

    creados = rotados = sin_cambios = convertidos = 0
    errores: list[dict] = []
    # Códigos ya resueltos por conversión (con éxito o con error): el loop
    # principal los saltea para no duplicar conteos ni repetir errores por nivel.
    codigos_resueltos: set[str] = set()

    if convertir_a_nivelado:
        por_codigo: dict[str, list[Galeno]] = {}
        for g in origen_galenos:
            por_codigo.setdefault(g.codigo, []).append(g)
        for codigo, rows in por_codigo.items():
            if rows[0].nivel is None:
                continue
            destino_viejo = await buscar_galeno_vigente(
                db, obra_social_nro_destino, codigo, None
            )
            if destino_viejo is None:
                continue
            try:
                await _convertir_destino_a_nivelado(
                    db, destino_viejo, rows, obra_social_nro_destino, vigencia_desde
                )
                convertidos += 1
            except ValueError as e:
                errores.append({"codigo": codigo, "nivel": None, "motivo": str(e)})
            codigos_resueltos.add(codigo)

    for g in origen_galenos:
        if g.codigo in codigos_resueltos:
            continue
        try:
            destino_g = await buscar_galeno_vigente(
                db, obra_social_nro_destino, g.codigo, g.nivel
            )
            if destino_g is None:
                if await _hay_mezcla_niveles(
                    db, obra_social_nro_destino, g.codigo, g.nivel
                ):
                    errores.append({
                        "codigo": g.codigo, "nivel": g.nivel,
                        "motivo": (
                            "el destino tiene este galeno sin nivel — activá "
                            "'Reemplazar galenos sin nivel del destino por los niveles "
                            "del origen' para convertirlo a nivelado"
                        ),
                    })
                    continue
                db.add(Galeno(
                    obra_social_nro=obra_social_nro_destino,
                    codigo=g.codigo,
                    nombre=g.nombre,
                    nivel=g.nivel,
                    vigencia_desde=vigencia_desde,
                    vigencia_hasta=None,
                    valor_unitario=g.valor_unitario,
                    unidades_honorarios=g.unidades_honorarios,
                    unidades_ayudante=g.unidades_ayudante,
                    unidades_gastos=g.unidades_gastos,
                ))
                await db.flush()
                creados += 1
            elif _galenos_iguales(destino_g, g, solo_valor=solo_valor):
                sin_cambios += 1
            else:
                await _rotar_galeno_destino(
                    db, destino_g, g, vigencia_desde, solo_valor=solo_valor
                )
                rotados += 1
        except ValueError as e:
            errores.append({"codigo": g.codigo, "nivel": g.nivel, "motivo": str(e)})

    return {
        "total_origen": len(origen_galenos),
        "creados": creados,
        "rotados": rotados,
        "sin_cambios": sin_cambios,
        "convertidos": convertidos,
        "errores": errores,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Seed de valores NN por rangos de código (galeno de honorarios + galeno de gastos)
# ─────────────────────────────────────────────────────────────────────────────
#
# Mapeo rango → galeno. Ambos ejes cubren TODO 1..419999 sin huecos.
# El galeno de honorarios se usa también para el Ayudante (mismo VU, otra unidad).
_RANGOS_HONORARIOS: list[tuple[int, int, str]] = [
    (1, 139999, "galeno_quirurgico"),
    (140000, 179999, "galeno_practica"),
    (180000, 189999, "galeno_radiologico"),
    (190000, 339999, "galeno_practica"),
    (340000, 349999, "galeno_radiologico"),
    (350000, 419999, "galeno_practica"),
]
# Ojo: los rangos específicos (bioquimico/radiologico) van ANTES que gasto_otros,
# porque los de bioquimico son subconjunto de los de gasto_otros (precedencia).
_RANGOS_GASTOS: list[tuple[int, int, str]] = [
    (1, 139999, "gasto_quirurgico"),
    (150000, 159999, "gasto_bioquimico"),
    (230000, 249999, "gasto_bioquimico"),
    (180000, 189999, "gasto_radiologico"),
    (340000, 349999, "gasto_radiologico"),
    (140000, 179999, "gasto_otros"),
    (190000, 339999, "gasto_otros"),
    (350000, 419999, "gasto_otros"),
]
# Todos los galenos base que la OS necesita tener cargados
_GALENOS_NN_CODIGOS: set[str] = (
    {c for _, _, c in _RANGOS_HONORARIOS} | {c for _, _, c in _RANGOS_GASTOS}
)
# Rango global cubierto (fuera de esto el código no es candidato)
_NN_RANGO_MIN, _NN_RANGO_MAX = 1, 419999

# Nombre para mostrar de cada galeno base — mismo criterio que `slugify_codigo`
# pero en el sentido inverso (acentos que el slug no puede reconstruir solo).
_GALENOS_NN_NOMBRES: dict[str, str] = {
    "galeno_quirurgico": "Galeno Quirúrgico",
    "galeno_practica": "Galeno Práctica",
    "galeno_radiologico": "Galeno Radiológico",
    "gasto_quirurgico": "Gasto Quirúrgico",
    "gasto_bioquimico": "Gasto Bioquímico",
    "gasto_radiologico": "Gasto Radiológico",
    "gasto_otros": "Gasto Otros",
}


def _galeno_por_rango(n: int, rangos: list[tuple[int, int, str]]) -> Optional[str]:
    """codigo del galeno del primer rango que contiene a n (None si ninguno)."""
    for lo, hi, cod in rangos:
        if lo <= n <= hi:
            return cod
    return None


class ComponentesNNNoDisponibles(Exception):
    """El código no admite componentes NN automáticos (motivo en el mensaje)."""


def componentes_nn(nom: NomencladorCMC, galenos: dict[str, Galeno]) -> list[dict]:
    """Los 3 componentes calculables de un valor NN de `nom`, con los galenos base
    de la OS (`galenos`: vigentes, nivel NULL, por código). Regla única que usan la
    generación masiva (`generar_valores_nn_por_rangos`) y el alta manual (modal de
    Por obra social, vía `componentes_nn_sugeridos`):
      - Honorarios → galeno de honorarios del rango, cantidad = unidades_honorarios
      - Ayudante   → el MISMO galeno de honorarios,  cantidad = unidades_ayudante
      - Gastos     → galeno de gastos del rango,     cantidad = unidades_gastos
    Cantidad por concepto: galeno.unidades_<c> → nn.unidades_<c> → 0. El rango se
    calcula con el código DEL COLEGIO.

    Lanza `ComponentesNNNoDisponibles` si el código no es numérico, está fuera de
    1..419999, no tiene Nomenclador Nacional vinculado o la OS no tiene vigente
    alguno de los dos galenos del rango.
    """
    nn = nom.nomenclador_nacional
    if nn is None:
        raise ComponentesNNNoDisponibles(
            f"El código {nom.codigo} no está vinculado a un código del Nomenclador Nacional"
        )
    if not nom.codigo.isdigit() or not (_NN_RANGO_MIN <= int(nom.codigo) <= _NN_RANGO_MAX):
        raise ComponentesNNNoDisponibles(
            f"El código {nom.codigo} está fuera de los rangos del Nomenclador Nacional "
            f"({_NN_RANGO_MIN}–{_NN_RANGO_MAX})"
        )
    n = int(nom.codigo)
    cod_hon = _galeno_por_rango(n, _RANGOS_HONORARIOS)
    cod_gas = _galeno_por_rango(n, _RANGOS_GASTOS)
    g_hon, g_gas = galenos.get(cod_hon), galenos.get(cod_gas)
    if g_hon is None or g_gas is None:
        faltan = [
            _GALENOS_NN_NOMBRES.get(c, c)
            for c, g in ((cod_hon, g_hon), (cod_gas, g_gas)) if g is None
        ]
        raise ComponentesNNNoDisponibles(
            f"La obra social no tiene vigente: {', '.join(faltan)}"
        )

    def _cant(attr: str, galeno: Galeno) -> Decimal:
        return getattr(galeno, attr) or getattr(nn, attr) or Decimal("0")

    return [
        {"concepto": "Honorarios", "galeno_id": g_hon.id,
         "cantidad": _cant("unidades_honorarios", g_hon), "orden": 0},
        {"concepto": "Gastos", "galeno_id": g_gas.id,
         "cantidad": _cant("unidades_gastos", g_gas), "orden": 1},
        {"concepto": "Ayudante", "galeno_id": g_hon.id,
         "cantidad": _cant("unidades_ayudante", g_hon), "orden": 2},
    ]


async def _galenos_base_nn(db: AsyncSession, obra_social_nro: int) -> dict[str, Galeno]:
    """Galenos base NN vigentes de la OS (nivel NULL), indexados por código."""
    return {
        g.codigo: g
        for g in (await db.execute(
            select(Galeno).where(
                Galeno.obra_social_nro == obra_social_nro,
                Galeno.codigo.in_(_GALENOS_NN_CODIGOS),
                Galeno.nivel.is_(None),
                Galeno.vigencia_hasta.is_(None),
                Galeno.activo == True,
            )
        )).scalars()
    }


async def componentes_nn_sugeridos(
    db: AsyncSession, obra_social_nro: int, nomenclador_id: int
) -> list[dict]:
    """Componentes NN de un código para una OS, para precargar el alta manual.
    Todo motivo para no poder sugerirlos sale como `ComponentesNNNoDisponibles`."""
    nom = await db.get(NomencladorCMC, nomenclador_id)
    if nom is None:
        raise ComponentesNNNoDisponibles("Código de nomenclador no encontrado")
    return componentes_nn(nom, await _galenos_base_nn(db, obra_social_nro))


async def crear_galenos_base(
    obra_social_nro: int, vigencia_desde: datetime.date, db: AsyncSession,
) -> list[Galeno]:
    """Crea, en $0, los 7 galenos base que `generar_valores_nn_por_rangos` necesita
    para poder sembrar el nomenclador NN de una OS recién creada.

    Idempotente: si alguno ya está vigente (por ejemplo, un reintento tras un error
    parcial) no lo duplica. $0 es a propósito — es un placeholder hasta que alguien
    actualice el precio real desde `ActualizarPreciosGalenos`; por eso `generar_
    valores_nn_por_rangos` necesita `permitir_valor_cero=True` para no rechazarlos."""
    ya_vigentes = {
        g.codigo
        for g in (await db.execute(
            select(Galeno).where(
                Galeno.obra_social_nro == obra_social_nro,
                Galeno.codigo.in_(_GALENOS_NN_CODIGOS),
                Galeno.nivel.is_(None),
                Galeno.vigencia_hasta.is_(None),
                Galeno.activo == True,
            )
        )).scalars()
    }
    creados = []
    for codigo in sorted(_GALENOS_NN_CODIGOS - ya_vigentes):
        g = Galeno(
            obra_social_nro=obra_social_nro,
            codigo=codigo,
            nombre=_GALENOS_NN_NOMBRES[codigo],
            nivel=None,
            vigencia_desde=vigencia_desde,
            vigencia_hasta=None,
            valor_unitario=Decimal("0"),
            activo=True,
        )
        db.add(g)
        creados.append(g)
    if creados:
        await db.flush()
    return creados


async def generar_valores_nn_por_rangos(
    obra_social_nro: int,
    vigencia_desde: datetime.date,
    db: AsyncSession,
    *,
    permitir_valor_cero: bool = False,
) -> dict:
    """
    Crea (o recrea) los valores NN de una OS para todo código del Colegio en
    1..419999 vinculado (`NomencladorCMC.nomenclador_nacional_id`) a un
    Nomenclador Nacional activo con al menos una unidad (unidades_*) no nula.

    Cada valor lleva 3 componentes calculables:
      - Honorarios → galeno de honorarios del rango, cantidad = unidades_honorarios
      - Ayudante   → el MISMO galeno de honorarios, cantidad = unidades_ayudante
      - Gastos     → galeno de gastos del rango, cantidad = unidades_gastos
    Cantidad por concepto: galeno.unidades_<c> → nn.unidades_<c> → 0. El rango
    (qué galeno corresponde) se sigue calculando con el código DEL COLEGIO, no
    con el código nacional — dos códigos del Colegio para el mismo NN pueden caer
    en rangos distintos si numéricamente están lejos entre sí.

    Conflicto (ya hay un NN activo para (OS, código)): cierra el vigente y crea el nuevo
    (rota vigencia). Partial-success: los ítems con galeno faltante van a `errores`.
    No comitea: corre dentro de la transacción del caller.

    `permitir_valor_cero=True` salta el chequeo 1b: lo usa únicamente el alta
    automática de una OS nueva (`crear_galenos_base` deja los 7 galenos en $0 a
    propósito) — todo Valor NN sale en $0 hasta que se actualicen los precios de
    verdad, momento en el que rotan solos. Para el resto de los llamadores (endpoint
    manual, CSV) el chequeo sigue de pie: un $0 ahí normalmente es un error de carga.
    """
    # 1. Galenos base vigentes de la OS (nivel NULL), indexados por codigo
    galenos = await _galenos_base_nn(db, obra_social_nro)

    # 1b. Rechazar si algún galeno base tiene VU = 0 (no sirve para calcular precios)
    if not permitir_valor_cero:
        galenos_en_cero = [cod for cod, g in galenos.items() if g.valor_unitario == Decimal("0")]
        if galenos_en_cero:
            raise ValueError(
                f"Los siguientes galenos tienen valor_unitario = 0 en OS {obra_social_nro}: "
                f"{', '.join(sorted(galenos_en_cero))}. Actualizá el precio antes de generar."
            )

    candidatos = await _candidatos_nn(db)

    corte = vigencia_desde - datetime.timedelta(days=1)
    total = creados = recreados = 0
    errores: list[dict] = []

    # Paso 1 del gate (quién puede cobrar): se siembra desde el catálogo del código
    # SOLO en los pares que todavía no tienen nada configurado en esta OS, así una
    # regeneración no pisa lo que se editó a mano. Ver `_habilitacion_a_sembrar`.
    habilitacion = await _estado_habilitacion_os(db, obra_social_nro)
    plantillas = await _plantillas_por_codigo(db)
    habilitaciones_sembradas = 0

    for nom in candidatos:
        nn = nom.nomenclador_nacional
        total += 1

        try:
            componentes = componentes_nn(nom, galenos)
        except ComponentesNNNoDisponibles as e:
            errores.append({"codigo": nom.codigo, "motivo": str(e)})
            continue

        # Conflicto: cerrar el NN activo previo (cerrar y recrear)
        existente = (await db.execute(
            select(Valor).where(
                Valor.obra_social_nro == obra_social_nro,
                Valor.nomenclador_id == nom.id,
                Valor.origen == "NN",
                Valor.especialidad_id_colegio.is_(None),
                Valor.estado == "activo",
            )
        )).scalars().first()
        if existente is not None:
            existente.estado = "cerrado"
            existente.vigencia_hasta = corte
            fecha_corte = corte
            recreados += 1
        else:
            fecha_corte = None
            creados += 1

        sin_restriccion, esps, sembrada = _habilitacion_a_sembrar(nom, habilitacion, plantillas)
        for esp in esps:
            db.add(ValorEspecialidad(
                obra_social_nro=obra_social_nro, codigo=nom.codigo,
                especialidad_id_colegio=esp,
            ))
        habilitaciones_sembradas += sembrada

        await persistir_valor(
            db,
            Valor(
                obra_social_nro=obra_social_nro,
                nomenclador_id=nom.id,
                origen="NN",
                codigo=nom.codigo,
                descripcion=nn.descripcion,
                nivel=None,
                complejidad=None,
                especialidad_id_colegio=None,
                sin_restriccion_especialidad=sin_restriccion,
                por_presupuesto=False,
                vigencia_desde=vigencia_desde,
                vigencia_hasta=None,
                estado="activo",
            ),
            componentes,
            motivo="carga_inicial",
            # Redundante pasar la vigencia aparte: `regenerar_historial_por_valores`
            # toma la del propio valor cuando no se le fuerza otra.
            fecha_corte=fecha_corte,
        )

    return {
        "total_candidatos": total,
        "creados": creados,
        "recreados": recreados,
        "habilitaciones_sembradas": habilitaciones_sembradas,
        "errores": errores,
    }


class _HabilitacionOS:
    """Qué pares (código) de una OS ya tienen configurado el paso 1 del gate."""

    def __init__(self, configurados: set[str], sin_restriccion: set[str]):
        self.configurados = configurados
        self.sin_restriccion = sin_restriccion


async def _estado_habilitacion_os(db: AsyncSession, obra_social_nro: int) -> _HabilitacionOS:
    con_especialidades = set((await db.execute(
        select(ValorEspecialidad.codigo).where(
            ValorEspecialidad.obra_social_nro == obra_social_nro
        ).distinct()
    )).scalars())
    sin_restriccion = set((await db.execute(
        select(Valor.codigo).where(
            Valor.obra_social_nro == obra_social_nro,
            Valor.estado == "activo",
            Valor.sin_restriccion_especialidad == True,
        ).distinct()
    )).scalars())
    return _HabilitacionOS(con_especialidades | sin_restriccion, sin_restriccion)


async def _candidatos_nn(db: AsyncSession) -> list[NomencladorCMC]:
    """Códigos que llevan NN: del Colegio, activos, numéricos en 1..419999 y
    vinculados a un Nomenclador Nacional activo con al menos una unidad no nula.
    `nomenclador_nacional` carga con joined (ver modelo): sin query extra por fila."""
    todos = (await db.execute(
        select(NomencladorCMC).where(
            NomencladorCMC.activo == True,
            NomencladorCMC.nomenclador_nacional_id.is_not(None),
        )
    )).scalars().all()
    return [
        nom for nom in todos
        if nom.nomenclador_nacional is not None
        and nom.nomenclador_nacional.activo
        and (
            nom.nomenclador_nacional.unidades_honorarios is not None
            or nom.nomenclador_nacional.unidades_ayudante is not None
            or nom.nomenclador_nacional.unidades_gastos is not None
        )
        and nom.codigo.isdigit()
        and _NN_RANGO_MIN <= int(nom.codigo) <= _NN_RANGO_MAX
    ]


async def _plantillas_por_codigo(db: AsyncSession) -> dict[str, list[int]]:
    plantillas: dict[str, list[int]] = {}
    for codigo, esp in (await db.execute(
        select(
            NomencladorPlantillaEspecialidad.codigo,
            NomencladorPlantillaEspecialidad.especialidad_id_colegio,
        )
    )).all():
        plantillas.setdefault(codigo, []).append(esp)
    return plantillas


def _habilitacion_a_sembrar(
    nom: NomencladorCMC, habilitacion: _HabilitacionOS, plantillas: dict[str, list[int]],
) -> tuple[bool, list[int], bool]:
    """Paso 1 del gate para el NN de `nom`: `(sin_restriccion, especialidades a
    habilitar, se_sembró)`. Solo siembra si el par todavía no tiene nada
    configurado en la OS (no pisa lo editado a mano): "sin restricción" si el
    catálogo lo marca, si no las especialidades de la plantilla. Marca el código
    como configurado en `habilitacion`."""
    sin_restriccion = nom.codigo in habilitacion.sin_restriccion
    if nom.codigo in habilitacion.configurados:
        return sin_restriccion, [], False
    habilitacion.configurados.add(nom.codigo)
    if nom.sin_restriccion_especialidad:
        return True, [], True
    if plantillas.get(nom.codigo):
        return sin_restriccion, list(plantillas[nom.codigo]), True
    return sin_restriccion, [], False


async def completar_base_nn(
    obra_social_nro: int, vigencia_desde: datetime.date, db: AsyncSession,
) -> dict:
    """Completa la base del nomenclador NN de una OS SIN tocar lo que ya tiene:

    1. Los 7 galenos base (`galeno_quirurgico`, …, `gasto_otros`): los que falten se
       crean en $0; los que ya están vigentes se informan y no se tocan.
    2. Un Valor NN por cada código candidato (`_candidatos_nn`) que todavía no tenga
       un NN activo en la OS, con los componentes de `componentes_nn` y el paso 1
       del gate sembrado desde el catálogo (`_habilitacion_a_sembrar`). Los NN que
       ya existen se cuentan y no se tocan (a diferencia de
       `generar_valores_nn_por_rangos`, que los cierra y recrea).

    Todo en bloque (`persistir_valores_en_bloque`): una OS son ~2.200 códigos, y de
    a uno eran ~20.000 consultas — minutos contra la base de prod.

    Con los galenos en $0 los NN salen en $0; al cargar el precio real de cada
    galeno base los NN rotan solos. No comitea."""
    # 1. Galenos base
    previos = await _galenos_base_nn(db, obra_social_nro)
    galenos_existentes = [
        {"codigo": c, "nombre": g.nombre, "valor_unitario": g.valor_unitario,
         "vigencia_desde": g.vigencia_desde}
        for c, g in sorted(previos.items())
    ]
    creados = await crear_galenos_base(obra_social_nro, vigencia_desde, db)
    galenos_creados = [{"codigo": g.codigo, "nombre": g.nombre} for g in creados]
    galenos = await _galenos_base_nn(db, obra_social_nro)

    # 2. Valores NN faltantes
    candidatos = await _candidatos_nn(db)
    con_nn = set((await db.execute(
        select(Valor.nomenclador_id).where(
            Valor.obra_social_nro == obra_social_nro,
            Valor.origen == "NN",
            Valor.especialidad_id_colegio.is_(None),
            Valor.estado == "activo",
        )
    )).scalars())
    habilitacion = await _estado_habilitacion_os(db, obra_social_nro)
    plantillas = await _plantillas_por_codigo(db)

    filas: list[tuple[dict, list[dict]]] = []
    habilitar: list[dict] = []
    habilitaciones_sembradas = nn_existentes = 0
    errores: list[dict] = []
    for nom in candidatos:
        if nom.id in con_nn:
            nn_existentes += 1
            continue
        try:
            componentes = componentes_nn(nom, galenos)
        except ComponentesNNNoDisponibles as e:
            errores.append({"codigo": nom.codigo, "motivo": str(e)})
            continue
        sin_restriccion, esps, sembrada = _habilitacion_a_sembrar(nom, habilitacion, plantillas)
        habilitaciones_sembradas += sembrada
        habilitar += [
            {"obra_social_nro": obra_social_nro, "codigo": nom.codigo,
             "especialidad_id_colegio": esp}
            for esp in esps
        ]
        filas.append((
            {
                "obra_social_nro": obra_social_nro, "nomenclador_id": nom.id,
                "origen": "NN", "codigo": nom.codigo,
                "descripcion": nom.nomenclador_nacional.descripcion,
                "nivel": None, "complejidad": None, "especialidad_id_colegio": None,
                "sin_restriccion_especialidad": sin_restriccion, "por_presupuesto": False,
                "vigencia_desde": vigencia_desde,
            },
            componentes,
        ))

    if habilitar:
        await db.execute(insert(ValorEspecialidad), habilitar)
    await persistir_valores_en_bloque(db, filas, motivo="carga_inicial")

    return {
        "galenos_creados": galenos_creados,
        "galenos_existentes": galenos_existentes,
        "total_candidatos": len(candidatos),
        "nn_creados": len(filas),
        "nn_existentes": nn_existentes,
        "habilitaciones_sembradas": habilitaciones_sembradas,
        "errores": errores,
    }


async def sembrar_nomenclador_nuevo(
    obra_social_nro: int, vigencia_desde: datetime.date, db: AsyncSession,
) -> dict:
    """Deja una OS recién creada con nomenclador NN operativo desde el día uno: los
    7 galenos base en $0 y todos los Valor NN calculados contra ellos. Es
    `completar_base_nn` sobre una OS vacía (no hay nada previo que respetar).

    Todo sale en $0 hasta que alguien cargue el precio real de cada galeno desde
    `ActualizarPreciosGalenos` — ahí los Valor NN rotan solos (mismo mecanismo que
    cualquier otro cambio de precio de galeno). No comitea: el caller decide si
    esto va en la misma transacción del alta de la OS o en una aparte."""
    r = await completar_base_nn(obra_social_nro, vigencia_desde, db)
    return {
        "galenos_creados": len(r["galenos_creados"]),
        "total_candidatos": r["total_candidatos"],
        "creados": r["nn_creados"],
        "recreados": 0,
        "habilitaciones_sembradas": r["habilitaciones_sembradas"],
        "errores": r["errores"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Validaciones de consistencia galeno ↔ valor
# ─────────────────────────────────────────────────────────────────────────────

def modalidad_de(componentes: list) -> str:
    """
    Modalidad de la ecuación (los componentes son homogéneos por validación):
    'galeno' si referencian galenos, 'fijo' si son precios embebidos.
    """
    return "galeno" if any(c.galeno_id is not None for c in componentes) else "fijo"


class NivelInconsistenteError(Exception):
    """El nivel del galeno no coincide con el nivel del valor."""
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def validar_nivel_galeno(galeno: Galeno, nivel_valor: Optional[int]) -> None:
    """
    Un galeno nivelado solo puede usarse en un Valor del mismo nivel.
    Un galeno sin nivel puede usarse en cualquier Valor.
    """
    if galeno.nivel is None:
        return
    if nivel_valor is None:
        raise NivelInconsistenteError(
            f"El galeno '{galeno.codigo}' nivel {galeno.nivel} requiere que el valor "
            f"tenga nivel asignado"
        )
    if galeno.nivel != nivel_valor:
        raise NivelInconsistenteError(
            f"El galeno '{galeno.codigo}' es nivel {galeno.nivel} pero el valor "
            f"es nivel {nivel_valor}"
        )


async def buscar_galeno_vigente(
    db: AsyncSession,
    obra_social_nro: int,
    codigo: str,
    nivel: Optional[int],
) -> Optional[Galeno]:
    """Galeno activo y con vigencia abierta para (OS, codigo, nivel)."""
    stmt = select(Galeno).where(
        Galeno.obra_social_nro == obra_social_nro,
        Galeno.codigo == codigo,
        Galeno.nivel.is_(None) if nivel is None else Galeno.nivel == nivel,
        Galeno.vigencia_hasta.is_(None),
        Galeno.activo == True,
    )
    return (await db.execute(stmt)).scalars().first()


# ─────────────────────────────────────────────────────────────────────────────
# Lookup de precio
# ─────────────────────────────────────────────────────────────────────────────

class LookupError(Exception):
    def __init__(self, message: str, status_code: int = 422, sin_precio: bool = False):
        self.message = message
        self.status_code = status_code
        # True SOLO cuando el rechazo es por falta de precio (no habilitación, no
        # fecha, no código/médico inexistente) — es lo único que
        # `settings.CARGA_SIN_PRECIO` puede pasar por alto. Ver resolver_precio.
        self.sin_precio = sin_precio
        super().__init__(message)


_CAMPOS_ESPECIALIDAD = (
    "NRO_ESPECIALIDAD",   # ojo: la primera columna legacy NO lleva sufijo "1"
    "NRO_ESPECIALIDAD2",
    "NRO_ESPECIALIDAD3",
    "NRO_ESPECIALIDAD4",
    "NRO_ESPECIALIDAD5",
    "NRO_ESPECIALIDAD6",
)


def _especialidades_medico(medico: ListadoMedico) -> list[int]:
    """Todas las especialidades cargadas del médico (0 = slot vacío)."""
    out = []
    for campo in _CAMPOS_ESPECIALIDAD:
        esp = getattr(medico, campo, None)
        if esp:
            out.append(esp)
    return out


async def _validar_habilitacion_medico(
    db: AsyncSession,
    medico: ListadoMedico,
    nomenclador: NomencladorCMC,
    fecha: datetime.date,
    obra_social_nro: Optional[int] = None,
) -> None:
    """
    Gate de habilitación (¿puede hacer la práctica?). El precio que cobra lo
    deciden después las variantes de Valor.

    Orden de evaluación:
    1. inhabilita vigente                        → rechazar
    2. habilita vigente                          → permitir
    3. algún Valor activo de (OS, código)
       con sin_restriccion_especialidad = True   → permitir
    4. especialidad habilitada para (OS, código) → permitir
    5. ninguna                                   → rechazar

    `sin_restriccion` y la especialidad habilitada viven en `nm_valores` /
    `nm_valor_especialidad`, por obra social — sin `obra_social_nro` no hay nada
    concreto contra qué evaluarlas, así que se rechaza directo (en la práctica
    `lookup_precio`, el único llamador real, siempre manda una OS).
    """
    vigencia_ok = and_(
        (MedicoCodigoHabilitado.vigencia_desde.is_(None)) |
        (MedicoCodigoHabilitado.vigencia_desde <= fecha),
        (MedicoCodigoHabilitado.vigencia_hasta.is_(None)) |
        (MedicoCodigoHabilitado.vigencia_hasta >= fecha),
    )

    stmt_inh = select(MedicoCodigoHabilitado.id).where(
        MedicoCodigoHabilitado.medico_id == medico.ID,
        MedicoCodigoHabilitado.nomenclador_id == nomenclador.id,
        MedicoCodigoHabilitado.tipo == "inhabilita",
        MedicoCodigoHabilitado.activo == True,
        vigencia_ok,
    )
    if (await db.execute(stmt_inh)).first():
        raise LookupError("El médico está inhabilitado para este código")

    stmt_hab = select(MedicoCodigoHabilitado.id).where(
        MedicoCodigoHabilitado.medico_id == medico.ID,
        MedicoCodigoHabilitado.nomenclador_id == nomenclador.id,
        MedicoCodigoHabilitado.tipo == "habilita",
        MedicoCodigoHabilitado.activo == True,
        vigencia_ok,
    )
    if (await db.execute(stmt_hab)).first():
        return

    if obra_social_nro is None:
        raise LookupError(
            "No se puede evaluar la habilitación por especialidad sin una obra "
            "social en contexto"
        )

    if await par_sin_restriccion(db, nomenclador.codigo, obra_social_nro):
        return

    especialidades = _especialidades_medico(medico)
    if not especialidades:
        raise LookupError(
            "El médico no tiene especialidades cargadas en el Colegio, "
            "y este código se habilita por especialidad"
        )

    habilitadas = await especialidades_habilitadas_de(db, nomenclador.codigo, obra_social_nro)
    if not habilitadas & set(especialidades):
        raise LookupError("Este código no corresponde a las especialidades del médico")


async def listar_codigos_habilitados(
    db: AsyncSession,
    medico: ListadoMedico,
    q: Optional[str] = None,
    obra_social_nro: Optional[int] = None,
) -> list[dict]:
    """Todos los códigos que el médico puede facturar, con el mismo alcance que evalúa
    `_validar_habilitacion_medico` (para que este listado sea 100% consistente con lo
    que `lookup_precio` aceptaría después): por especialidad + excepción individual
    `habilita` + `sin_restriccion_especialidad`, menos excepción individual
    `inhabilita` (gana sobre todo lo demás). Filtra códigos activos; `q` busca por
    código.

    `obra_social_nro` acota especialidad/sin_restriccion a lo que rige EN esa OS
    (`nm_valor_especialidad` / `Valor.sin_restriccion_especialidad`) y también la
    descripción: si esa OS nombra el código distinto en su propio `nm_valores`, esa es
    la que se devuelve en vez de la del catálogo. **Sin obra social el listado es la
    UNIÓN**: aparece todo código que alguna regla de CUALQUIER OS habilite, aunque
    para una puntual no aplique, y la descripción es siempre la del catálogo. Es
    deliberado — este listado es "mis códigos" del médico, que no se mira parado en
    una obra social; el rechazo fino ocurre al cotizar, que es donde sí hay OS.

    Cada código trae además las especialidades DEL MÉDICO que lo habilitan (puede ser
    más de una si el código está vinculado a varias especialidades que el médico
    tiene, y queda vacía si solo entra por excepción individual o sin restricción)."""
    fecha = datetime.date.today()
    vigencia_ok = and_(
        (MedicoCodigoHabilitado.vigencia_desde.is_(None)) |
        (MedicoCodigoHabilitado.vigencia_desde <= fecha),
        (MedicoCodigoHabilitado.vigencia_hasta.is_(None)) |
        (MedicoCodigoHabilitado.vigencia_hasta >= fecha),
    )

    inhabilita_ids = set((await db.execute(
        select(MedicoCodigoHabilitado.nomenclador_id).where(
            MedicoCodigoHabilitado.medico_id == medico.ID,
            MedicoCodigoHabilitado.tipo == "inhabilita",
            MedicoCodigoHabilitado.activo == True,
            vigencia_ok,
        )
    )).scalars().all())

    habilita_ids = set((await db.execute(
        select(MedicoCodigoHabilitado.nomenclador_id).where(
            MedicoCodigoHabilitado.medico_id == medico.ID,
            MedicoCodigoHabilitado.tipo == "habilita",
            MedicoCodigoHabilitado.activo == True,
            vigencia_ok,
        )
    )).scalars().all())

    # (codigo, especialidad_id_colegio) — se conserva el par para poder armar, más
    # abajo, QUÉ especialidad del médico habilita cada código (puede ser más de una).
    especialidades = _especialidades_medico(medico)
    especialidad_rows: list[tuple[str, int]] = []
    if especialidades:
        cond_esp = [ValorEspecialidad.especialidad_id_colegio.in_(especialidades)]
        if obra_social_nro is not None:
            cond_esp.append(ValorEspecialidad.obra_social_nro == obra_social_nro)
        especialidad_rows = (await db.execute(
            select(ValorEspecialidad.codigo, ValorEspecialidad.especialidad_id_colegio)
            .where(*cond_esp)
        )).all()
    codigos_con_especialidad = {c for c, _ in especialidad_rows}

    cond_sr = [Valor.sin_restriccion_especialidad == True, Valor.estado == "activo"]
    if obra_social_nro is not None:
        cond_sr.append(Valor.obra_social_nro == obra_social_nro)
    codigos_sin_restriccion = set((await db.execute(
        select(Valor.codigo).where(*cond_sr).distinct()
    )).scalars().all())

    # Traducir los sets por CÓDIGO a ids de nm_nomenclador con un solo query — el resto
    # de la función (habilita/inhabilita) ya trabaja por id, vía MedicoCodigoHabilitado.
    codigos_relevantes = codigos_con_especialidad | codigos_sin_restriccion
    id_por_codigo: dict[str, int] = {}
    if codigos_relevantes:
        id_por_codigo = {
            codigo: nid for codigo, nid in (await db.execute(
                select(NomencladorCMC.codigo, NomencladorCMC.id)
                .where(NomencladorCMC.codigo.in_(codigos_relevantes))
            )).all()
        }
    especialidad_ids = {id_por_codigo[c] for c in codigos_con_especialidad if c in id_por_codigo}
    sin_restriccion_ids = {id_por_codigo[c] for c in codigos_sin_restriccion if c in id_por_codigo}
    especialidades_por_codigo: dict[int, list[int]] = {}
    for c, eid in especialidad_rows:
        if c in id_por_codigo:
            especialidades_por_codigo.setdefault(id_por_codigo[c], []).append(eid)

    ids_finales = (habilita_ids | especialidad_ids | sin_restriccion_ids) - inhabilita_ids
    if not ids_finales:
        return []

    stmt = (
        select(NomencladorCMC)
        .where(NomencladorCMC.id.in_(ids_finales), NomencladorCMC.activo == True)
        .order_by(NomencladorCMC.codigo.asc())
    )
    if q:
        stmt = stmt.where(NomencladorCMC.codigo.ilike(f"%{q}%"))
    codigos = list((await db.execute(stmt)).scalars().all())

    # Nombres de las especialidades DEL MÉDICO (alcanza con resolver esas, no todo el catálogo).
    nombre_map: dict[int, str] = {}
    if especialidades:
        esp_rows = (await db.execute(
            select(Especialidad.ID_COLEGIO_ESPE, Especialidad.ESPECIALIDAD)
            .where(Especialidad.ID_COLEGIO_ESPE.in_(especialidades))
        )).all()
        nombre_map = {int(eid): nombre for eid, nombre in esp_rows}

    # Descripción pactada con esta OS (misma precedencia que `descripcion_efectiva` y
    # que el autocomplete de `buscar_nomenclador`): sin esto, este listado mostraba
    # siempre el texto del catálogo del Colegio aunque la OS elegida nombre el código
    # distinto en su propio `nm_valores` (p. ej. un código que el Colegio cataloga con
    # una descripción y que una OS puntual pactó para nombrar otra práctica).
    desc_por_codigo: dict[int, str] = {}
    if obra_social_nro is not None:
        desc_rows = (await db.execute(
            select(Valor.nomenclador_id, func.max(Valor.descripcion))
            .where(
                Valor.obra_social_nro == obra_social_nro,
                Valor.nomenclador_id.in_(ids_finales),
                Valor.estado == "activo",
                Valor.descripcion.is_not(None),
                Valor.descripcion != "",
            )
            .group_by(Valor.nomenclador_id)
        )).all()
        desc_por_codigo = {nid: desc for nid, desc in desc_rows}

    faltan = {(c.codigo, obra_social_nro) for c in codigos if not desc_por_codigo.get(c.id)}
    legacy = await descripciones_legacy(db, faltan) if faltan else {}

    return [
        {
            "codigo": c.codigo,
            "descripcion": desc_por_codigo.get(c.id) or legacy.get((c.codigo, obra_social_nro), ""),
            "categoria": c.categoria,
            "complejidad": c.complejidad,
            "especialidades": [
                {"id": eid, "nombre": nombre_map.get(eid)}
                for eid in especialidades_por_codigo.get(c.id, [])
            ],
        }
        for c in codigos
    ]


async def get_cantidad_ayudantes(
    db: AsyncSession, obra_social_nro: int, nomenclador_id: int, fecha: datetime.date,
) -> int:
    """Máximo de ayudantes admitidos por el Valor vigente activo para (OS, código).
    0 si es NULL o no hay valor (NULL = no lleva ayudantes). Usa MAX para ser robusto
    ante múltiples variantes del mismo código+OS (caso marginal)."""
    stmt = select(func.max(Valor.cantidad_ayudantes)).where(
        Valor.obra_social_nro == obra_social_nro,
        Valor.nomenclador_id == nomenclador_id,
        Valor.estado == "activo",
        Valor.vigencia_desde <= fecha,
        (Valor.vigencia_hasta.is_(None)) | (Valor.vigencia_hasta >= fecha),
    )
    val = (await db.execute(stmt)).scalar()
    return int(val) if val is not None else 0


async def lookup_precio(
    nomenclador_id: int,
    obra_social_nro: int,
    fecha: datetime.date,
    medico_id: int,
    db: AsyncSession,
    via: str = service_vias.VIA_TRADICIONAL,
) -> LookupPrecioOut:
    """
    Lookup directo en historial_precio_codigo + validación de habilitación del médico.
    Lanza LookupError con el motivo si alguna validación falla.
    """
    today = datetime.date.today()

    if fecha > today:
        raise LookupError("No se permiten prestaciones con fecha futura")

    if fecha < today - datetime.timedelta(days=182):
        raise LookupError("Prestación con más de 6 meses de atraso; use modo manual")

    medico = await db.get(ListadoMedico, medico_id)
    if not medico:
        raise LookupError("Médico no encontrado", 404)

    nomenclador = await db.get(NomencladorCMC, nomenclador_id)
    if not nomenclador:
        raise LookupError("Código no encontrado en el nomenclador", 404)

    # Etapa 3: el código tiene que estar dado de alta (y no suspendido) en la O.S.
    par = await _par_de(db, obra_social_nro, nomenclador_id)
    if par is not None and par.estado == "suspendido":
        raise LookupError("El código está suspendido en esta obra social")
    if par is None and not await _tiene_valor_activo(db, obra_social_nro, nomenclador_id):
        raise LookupError("El código no está dado de alta en esta obra social")

    # Gate de habilitación (¿puede hacer la práctica?)
    await _validar_habilitacion_medico(db, medico, nomenclador, fecha, obra_social_nro)

    # Variantes de precio vigentes a la fecha (una fila de historial por variante)
    stmt_hist = (
        select(HistorialPrecioCodigo)
        .where(
            HistorialPrecioCodigo.nomenclador_id == nomenclador_id,
            HistorialPrecioCodigo.obra_social_nro == obra_social_nro,
            HistorialPrecioCodigo.vigencia_desde <= fecha,
            (HistorialPrecioCodigo.vigencia_hasta.is_(None)) |
            (HistorialPrecioCodigo.vigencia_hasta >= fecha),
        )
        # Dentro de cada variante puede haber solapamiento por datos sucios:
        # nos quedamos con la fila más reciente por variante
        .order_by(HistorialPrecioCodigo.vigencia_desde.desc())
    )
    filas = (await db.execute(stmt_hist)).scalars().all()
    if not filas:
        # Dos casos bien distintos para el mismo "sin precio", y el prestador
        # necesita saber cuál es: si la obra social cargó el código alguna vez
        # (en otra fecha, otra variante) el problema es de vigencia — falta
        # actualizar el valor para este período; si nunca cargó nada, el código
        # directamente no está en su nomenclador. Sin esta distinción el mensaje
        # ("no tiene un valor vigente a esa fecha") sonaba a lo primero incluso
        # cuando era lo segundo.
        existe_algun_precio = (await db.execute(
            select(HistorialPrecioCodigo.id)
            .where(
                HistorialPrecioCodigo.nomenclador_id == nomenclador_id,
                HistorialPrecioCodigo.obra_social_nro == obra_social_nro,
            )
            .limit(1)
        )).scalar_one_or_none() is not None
        if existe_algun_precio:
            raise LookupError(
                "No existe vigencia correspondiente para este código",
                sin_precio=True,
            )
        if par is not None:
            raise LookupError(
                "El código está dado de alta en esta obra social pero todavía no tiene precio",
                sin_precio=True,
            )
        raise LookupError(
            "No existe ningún precio para este código",
            sin_precio=True,
        )

    # Una fila por variante (origen, especialidad): la más reciente (filas viene desc)
    por_variante: dict = {}
    for fila in filas:
        por_variante.setdefault((fila.origen, fila.especialidad_id_colegio), fila)

    # Perfil del médico: orden de slots (NRO_ESPECIALIDAD principal = índice 0)
    especialidades = _especialidades_medico(medico)
    slot_rank = {esp: i for i, esp in enumerate(especialidades)}
    # Las variantes sin especialidad pierden contra un match dentro del mismo origen
    _SLOT_SIN_ESP = len(especialidades) + 1

    # Una NE sin especialidad sólo existe en un par sin restricción (ver
    # validar_reglas_origen): es el precio para cualquier especialidad.
    sin_restriccion = any(
        f.origen == "NE" and f.especialidad_id_colegio is None for f in por_variante.values()
    ) and await par_sin_restriccion(db, nomenclador.codigo, obra_social_nro)

    def _aplicable(fila) -> bool:
        # NN nunca lleva especialidad: siempre aplicable. NE con especialidad aplica
        # si el médico la tiene; NE sin especialidad, sólo si el par es sin
        # restricción — misma condición que routes_reportes._aplicable.
        if fila.origen == "NN":
            return True
        if fila.especialidad_id_colegio is None:
            return sin_restriccion
        return fila.especialidad_id_colegio in slot_rank

    candidatas = [f for f in por_variante.values() if _aplicable(f)]
    if not candidatas:
        raise LookupError(
            "El valor vigente de este código exige una especialidad que el médico "
            "no tiene cargada",
            sin_precio=True,
        )

    def _orden(fila):
        # Menor gana: prioridad de origen (NE>NN) → match de especialidad por
        # orden de slots → vigencia más reciente como desempate final.
        rank = (
            slot_rank.get(fila.especialidad_id_colegio, _SLOT_SIN_ESP)
            if fila.especialidad_id_colegio is not None
            else _SLOT_SIN_ESP
        )
        return (prioridad_origen(fila.origen), rank, -fila.vigencia_desde.toordinal())

    historial = min(candidatas, key=_orden)

    valor = await db.get(Valor, historial.valores_id)

    componentes_out: List[ComponenteLookupOut] = [
        ComponenteLookupOut(
            componente_id=item.get("componente_id"),
            concepto=item["concepto"],
            tipo=item["tipo"],
            galeno_id=item.get("galeno_id"),
            galeno_codigo=item.get("galeno_codigo"),
            galeno_nivel=item.get("galeno_nivel"),
            cantidad=Decimal(item["cantidad"]),
            valor_unitario=Decimal(item["valor_unitario"]),
            # quantize defensivo: snapshots históricos previos al fix de precisión
            # pueden traer más de 2 decimales; se limpian solos al leerse.
            subtotal=quantize_money(item["subtotal"]),
        )
        for item in historial.componentes_snapshot
    ]

    try:
        componentes_out, precio_total, nivel_cotizado = await service_vias.ajustar_componentes_por_via(
            db, obra_social_nro, componentes_out, via,
        )
    except service_vias.ViaNoAplicableError as e:
        raise LookupError(e.message, e.status_code)

    # Todos los componentes suman (ya no hay opcionales): precio_base == precio_total.
    precio_base = precio_total

    legacy_desc = None
    if not (valor and valor.descripcion and valor.descripcion.strip()):
        mapa = await descripciones_legacy(db, {(nomenclador.codigo, obra_social_nro)})
        legacy_desc = mapa.get((nomenclador.codigo, obra_social_nro))

    return LookupPrecioOut(
        nomenclador_id=nomenclador_id,
        codigo_colegio=nomenclador.codigo,
        descripcion=descripcion_efectiva(valor, legacy_desc),
        obra_social_nro=obra_social_nro,
        nivel=valor.nivel if valor else None,
        origen=historial.origen,
        variante_especialidad_id=historial.especialidad_id_colegio,
        por_presupuesto=bool(valor and valor.por_presupuesto),
        requiere_autorizacion=requiere_autorizacion_efectiva(valor),
        fecha_practica=fecha,
        precio_base=precio_base,
        precio_total=precio_total,
        coseguro=valor.coseguro if valor else Decimal("0"),
        componentes=componentes_out,
        via=via,
        nivel_cotizado=nivel_cotizado,
    )
