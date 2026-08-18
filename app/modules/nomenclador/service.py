"""
Motor de generación y regeneración de historial_precio_codigo.

Todas las funciones públicas corren DENTRO de la transacción del caller.
Si cualquier paso falla, el rollback se propaga al caller automáticamente.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.money import quantize_money
from app.db.models.nomenclador_cmc import (
    Galeno,
    HistorialPrecioCodigo,
    MedicoCodigoHabilitado,
    NomencladorCMC,
    NomencladorEspecialidad,
    Valor,
    ValorComponente,
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
ORIGEN_PRIORIDAD: tuple[str, ...] = ("NE", "NNE", "NN")

# Rank para orígenes desconocidos: pierden contra cualquier origen conocido (fail-safe).
_PRIORIDAD_DESCONOCIDA = len(ORIGEN_PRIORIDAD)


def prioridad_origen(origen: str) -> int:
    """Menor = mayor prioridad. Origen desconocido → último (fail-safe)."""
    try:
        return ORIGEN_PRIORIDAD.index(origen)
    except ValueError:
        return _PRIORIDAD_DESCONOCIDA


# ─────────────────────────────────────────────────────────────────────────────
# Resolución del código contra una obra social
# ─────────────────────────────────────────────────────────────────────────────
#
# El código NO es identidad global: el mismo número puede nombrar prácticas distintas
# según la OS. `nm_nomenclador.obra_social_nro` separa las dos poblaciones y acá se
# elige entre ellas — misma forma que ORIGEN_PRIORIDAD: la especificidad gana.

async def resolver_nomenclador(
    db: AsyncSession, codigo: str, obra_social_nro: Optional[int]
) -> Optional[NomencladorCMC]:
    """Código + OS → fila del catálogo. Precedencia: propia de la OS > compartida.

    `obra_social_nro=None` (uso administrativo, sin OS en contexto) devuelve solo la
    fila compartida: adivinar cuál de las propias corresponde sería peor que no
    resolver. Devuelve None si el código no existe.
    """
    condicion_pertenencia = NomencladorCMC.obra_social_nro.is_(None)
    if obra_social_nro is not None:
        condicion_pertenencia = or_(
            condicion_pertenencia, NomencladorCMC.obra_social_nro == obra_social_nro
        )

    stmt = (
        select(NomencladorCMC)
        .where(
            NomencladorCMC.codigo == codigo,
            NomencladorCMC.activo.is_(True),
            condicion_pertenencia,
        )
        # obra_social_key = coalesce(obra_social_nro, -1): la fila propia (N > 0) queda
        # antes que la compartida (-1).
        .order_by(NomencladorCMC.obra_social_key.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def filtro_pertenencia(obra_social_nro: Optional[int]):
    """Condición SQL reusable para listar el catálogo visible por una OS: sus códigos
    propios más los compartidos. Sin OS → solo compartidos."""
    if obra_social_nro is None:
        return NomencladorCMC.obra_social_nro.is_(None)
    return or_(
        NomencladorCMC.obra_social_nro.is_(None),
        NomencladorCMC.obra_social_nro == obra_social_nro,
    )


def _nivel_pertenencia_especialidad(nomenclador_id: int, obra_social_nro: Optional[int]):
    """Sub-select con el `obra_social_key` que manda para ese código.

    MAX(obra_social_key) sobre las filas visibles: si la OS tiene reglas propias (key = N)
    gana sobre las compartidas (key = -1). Es la misma precedencia que `ORDER BY
    obra_social_key DESC` en `resolver_nomenclador`, pero aplicada a una relación 1:N:
    acá no se elige una fila, se elige el CONJUNTO de filas de un nivel.
    """
    E = NomencladorEspecialidad
    visibles = [E.nomenclador_id == nomenclador_id, E.activo.is_(True)]
    if obra_social_nro is None:
        visibles.append(E.obra_social_nro.is_(None))
    else:
        visibles.append(
            or_(E.obra_social_nro.is_(None), E.obra_social_nro == obra_social_nro)
        )
    return select(func.max(E.obra_social_key)).where(*visibles).scalar_subquery()


async def especialidades_habilitadas_de(
    db: AsyncSession, nomenclador_id: int, obra_social_nro: Optional[int]
) -> set[int]:
    """Especialidades que pueden facturar un código en el contexto de una obra social.

    Las reglas propias de la OS REEMPLAZAN a las compartidas, no se suman: si el Colegio
    dice "lo hacen patólogos" y Sancor dice "lo hacen oftalmólogos", para Sancor lo hacen
    oftalmólogos y punto. Sin reglas propias, valen las del Colegio.
    """
    E = NomencladorEspecialidad
    stmt = select(E.especialidad_id_colegio).where(
        E.nomenclador_id == nomenclador_id,
        E.activo.is_(True),
        E.obra_social_key == _nivel_pertenencia_especialidad(nomenclador_id, obra_social_nro),
    )
    return {row for row in (await db.execute(stmt)).scalars()}


def descripcion_efectiva(
    valor: Optional[Valor], nomenclador: Optional[NomencladorCMC]
) -> str:
    """Cómo se nombra el código en el contexto de una OS.

    La del valor pactado con esa obra social si la hay; si no, la del catálogo. Es la
    única precedencia de descripción del sistema — antes cada camino elegía una fuente
    distinta (el autocomplete el catálogo, el lookup de precio el valor) y mostraban
    textos diferentes para el mismo código.
    """
    if valor is not None and valor.descripcion and valor.descripcion.strip():
        return valor.descripcion
    if nomenclador is not None and nomenclador.descripcion:
        return nomenclador.descripcion
    return ""


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


def requiere_autorizacion_efectiva(
    valor: Optional[Valor], nomenclador: Optional[NomencladorCMC]
) -> bool:
    """¿La práctica necesita autorización previa de la obra social?

    Override del valor de esa OS > default del Colegio. Se compara contra None y no
    por truthiness a propósito: `False` en el valor significa "esta obra social dice
    que no" y tiene que ganarle al catálogo, mientras que `None` es "no opinó" y hereda.

    No confundir con `ObraManual.requiere_autorizacion` (validaciones), que es un
    todo-o-nada por obra social. Los dos niveles se combinan con OR en el gate de carga.
    """
    if valor is not None and valor.requiere_autorizacion is not None:
        return bool(valor.requiere_autorizacion)
    return bool(nomenclador.requiere_autorizacion) if nomenclador is not None else False


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

    precio_total = Decimal("0")
    snapshot = []

    for comp in componentes:
        if comp.galeno_id is not None:
            # calculable
            galeno = await db.get(Galeno, comp.galeno_id)
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


def _galeno_por_rango(n: int, rangos: list[tuple[int, int, str]]) -> Optional[str]:
    """codigo del galeno del primer rango que contiene a n (None si ninguno)."""
    for lo, hi, cod in rangos:
        if lo <= n <= hi:
            return cod
    return None


async def generar_valores_nn_por_rangos(
    obra_social_nro: int,
    vigencia_desde: datetime.date,
    db: AsyncSession,
) -> dict:
    """
    Crea (o recrea) los valores NN de una OS para todos los códigos numéricos de
    nm_nomenclador en 1..419999 con al menos una unidad (unidades_*) no nula.

    Cada valor lleva 3 componentes calculables:
      - Honorarios → galeno de honorarios del rango, cantidad = unidades_honorarios
      - Ayudante   → el MISMO galeno de honorarios, cantidad = unidades_ayudante
      - Gastos     → galeno de gastos del rango, cantidad = unidades_gastos
    Cantidad por concepto: galeno.unidades_<c> → nomenclador.unidades_<c> → 0.

    Conflicto (ya hay un NN activo para (OS, código)): cierra el vigente y crea el nuevo
    (rota vigencia). Partial-success: los ítems con galeno faltante van a `errores`.
    No comitea: corre dentro de la transacción del caller.
    """
    # 1. Galenos base vigentes de la OS (nivel NULL), indexados por codigo
    galenos = {
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

    # 1b. Rechazar si algún galeno base tiene VU = 0 (no sirve para calcular precios)
    galenos_en_cero = [cod for cod, g in galenos.items() if g.valor_unitario == Decimal("0")]
    if galenos_en_cero:
        raise ValueError(
            f"Los siguientes galenos tienen valor_unitario = 0 en OS {obra_social_nro}: "
            f"{', '.join(sorted(galenos_en_cero))}. Actualizá el precio antes de generar."
        )

    # 2. Candidatos: activos con >=1 unidad no nula (el rango se filtra en Python)
    candidatos = (await db.execute(
        select(NomencladorCMC).where(
            NomencladorCMC.activo == True,
            (NomencladorCMC.unidades_honorarios.is_not(None))
            | (NomencladorCMC.unidades_ayudante.is_not(None))
            | (NomencladorCMC.unidades_gastos.is_not(None)),
        )
    )).scalars().all()

    corte = vigencia_desde - datetime.timedelta(days=1)
    total = creados = recreados = 0
    errores: list[dict] = []

    for nom in candidatos:
        if not nom.codigo.isdigit():
            continue
        n = int(nom.codigo)
        if not (_NN_RANGO_MIN <= n <= _NN_RANGO_MAX):
            continue
        total += 1

        cod_hon = _galeno_por_rango(n, _RANGOS_HONORARIOS)
        cod_gas = _galeno_por_rango(n, _RANGOS_GASTOS)
        g_hon = galenos.get(cod_hon)
        g_gas = galenos.get(cod_gas)
        if g_hon is None or g_gas is None:
            faltan = [c for c, g in ((cod_hon, g_hon), (cod_gas, g_gas)) if g is None]
            errores.append({
                "codigo": nom.codigo,
                "motivo": f"galeno(s) no vigente(s) en la OS: {', '.join(faltan)}",
            })
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

        def _cant(concepto_attr: str, galeno: Galeno) -> Decimal:
            return (
                getattr(galeno, concepto_attr)
                or getattr(nom, concepto_attr)
                or Decimal("0")
            )

        valor = Valor(
            obra_social_nro=obra_social_nro,
            nomenclador_id=nom.id,
            origen="NN",
            codigo=nom.codigo,
            descripcion=None,  # hereda del catálogo (descripcion_efectiva)
            nivel=None,
            complejidad=None,
            especialidad_id_colegio=None,
            por_presupuesto=False,
            vigencia_desde=vigencia_desde,
            vigencia_hasta=None,
            estado="activo",
        )
        db.add(valor)
        await db.flush()

        db.add_all([
            ValorComponente(valor_id=valor.id, concepto="Honorarios",
                            galeno_id=g_hon.id, cantidad=_cant("unidades_honorarios", g_hon), orden=0),
            ValorComponente(valor_id=valor.id, concepto="Gastos",
                            galeno_id=g_gas.id, cantidad=_cant("unidades_gastos", g_gas), orden=1),
            ValorComponente(valor_id=valor.id, concepto="Ayudante",
                            galeno_id=g_hon.id, cantidad=_cant("unidades_ayudante", g_hon), orden=2),
        ])
        await db.flush()

        await regenerar_historial_por_valores(
            valor.id, fecha_corte, db, motivo="carga_inicial",
            nueva_vigencia_desde=vigencia_desde,
        )

    return {
        "total_candidatos": total,
        "creados": creados,
        "recreados": recreados,
        "errores": errores,
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
    1. inhabilita vigente                  → rechazar
    2. habilita vigente                    → permitir
    3. nomenclador.sin_restriccion = True  → permitir
    4. especialidad habilitada para el código EN ESA OS → permitir
    5. ninguna                             → rechazar

    `obra_social_nro` decide qué reglas de especialidad aplican (las propias de la OS
    reemplazan a las del Colegio). Omitirlo evalúa solo contra las compartidas.
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
        raise LookupError("Médico inhabilitado para este código")

    stmt_hab = select(MedicoCodigoHabilitado.id).where(
        MedicoCodigoHabilitado.medico_id == medico.ID,
        MedicoCodigoHabilitado.nomenclador_id == nomenclador.id,
        MedicoCodigoHabilitado.tipo == "habilita",
        MedicoCodigoHabilitado.activo == True,
        vigencia_ok,
    )
    if (await db.execute(stmt_hab)).first():
        return

    if nomenclador.sin_restriccion_especialidad:
        return

    especialidades = _especialidades_medico(medico)
    if not especialidades:
        raise LookupError("Médico sin especialidades cargadas; sin habilitación para este código")

    # Las reglas propias de esta OS, si las hay, reemplazan a las del Colegio: la misma
    # práctica puede exigir distinta especialidad según la obra social.
    habilitadas = await especialidades_habilitadas_de(db, nomenclador.id, obra_social_nro)
    if not habilitadas & set(especialidades):
        raise LookupError("Médico sin habilitación por especialidad para este código")


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
    código o descripción.

    `obra_social_nro` aplica las reglas de especialidad de esa OS (las propias
    reemplazan a las del Colegio). **Sin obra social el listado es la UNIÓN**: aparece
    todo código que alguna regla habilite, aunque para una OS puntual no aplique. Es
    deliberado — este listado es "mis códigos" del médico, que no se mira parado en una
    obra social; el rechazo fino ocurre al cotizar, que es donde sí hay OS.

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

    # (nomenclador_id, especialidad_id_colegio) — se conserva el par para poder armar,
    # más abajo, QUÉ especialidad del médico habilita cada código (puede ser más de una).
    especialidades = _especialidades_medico(medico)
    especialidad_rows: list[tuple[int, int]] = []
    if especialidades:
        E = NomencladorEspecialidad
        cond_esp = [
            E.especialidad_id_colegio.in_(especialidades),
            E.activo == True,
        ]
        if obra_social_nro is not None:
            # Precedencia por código: si esa OS tiene reglas propias, reemplazan a las
            # compartidas. El MAX correlacionado elige el nivel que manda para CADA
            # nomenclador_id (key N de la OS > key -1 del Colegio).
            E2 = aliased(NomencladorEspecialidad)
            nivel_que_manda = (
                select(func.max(E2.obra_social_key))
                .where(
                    E2.nomenclador_id == E.nomenclador_id,
                    E2.activo == True,
                    or_(E2.obra_social_nro.is_(None), E2.obra_social_nro == obra_social_nro),
                )
                .scalar_subquery()
            )
            cond_esp += [
                or_(E.obra_social_nro.is_(None), E.obra_social_nro == obra_social_nro),
                E.obra_social_key == nivel_que_manda,
            ]
        especialidad_rows = (await db.execute(
            select(E.nomenclador_id, E.especialidad_id_colegio).where(*cond_esp)
        )).all()
    especialidad_ids = {nid for nid, _ in especialidad_rows}
    especialidades_por_codigo: dict[int, list[int]] = {}
    for nid, eid in especialidad_rows:
        especialidades_por_codigo.setdefault(nid, []).append(eid)

    sin_restriccion_ids = set((await db.execute(
        select(NomencladorCMC.id).where(NomencladorCMC.sin_restriccion_especialidad == True)
    )).scalars().all())

    ids_finales = (habilita_ids | especialidad_ids | sin_restriccion_ids) - inhabilita_ids
    if not ids_finales:
        return []

    stmt = (
        select(NomencladorCMC)
        .where(NomencladorCMC.id.in_(ids_finales), NomencladorCMC.activo == True)
        .order_by(NomencladorCMC.codigo.asc())
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(NomencladorCMC.codigo.ilike(like), NomencladorCMC.descripcion.ilike(like)))
    codigos = list((await db.execute(stmt)).scalars().all())

    # Nombres de las especialidades DEL MÉDICO (alcanza con resolver esas, no todo el catálogo).
    nombre_map: dict[int, str] = {}
    if especialidades:
        esp_rows = (await db.execute(
            select(Especialidad.ID_COLEGIO_ESPE, Especialidad.ESPECIALIDAD)
            .where(Especialidad.ID_COLEGIO_ESPE.in_(especialidades))
        )).all()
        nombre_map = {int(eid): nombre for eid, nombre in esp_rows}

    return [
        {
            "codigo": c.codigo,
            "descripcion": c.descripcion,
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
        raise LookupError(
            "Sin precio registrado para ese código, obra social y fecha", sin_precio=True
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

    def _aplicable(fila) -> bool:
        if fila.especialidad_id_colegio is None:
            return True
        return fila.especialidad_id_colegio in slot_rank

    candidatas = [f for f in por_variante.values() if _aplicable(f)]
    if not candidatas:
        raise LookupError(
            "Sin precio para el perfil del médico: las variantes vigentes exigen "
            "una especialidad que no posee y no hay variante sin especialidad",
            sin_precio=True,
        )

    def _orden(fila):
        # Menor gana: prioridad de origen (NE>NNE>NN) → match de especialidad por
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

    return LookupPrecioOut(
        nomenclador_id=nomenclador_id,
        codigo_colegio=nomenclador.codigo,
        descripcion=descripcion_efectiva(valor, nomenclador),
        obra_social_nro=obra_social_nro,
        nivel=valor.nivel if valor else None,
        origen=historial.origen,
        variante_especialidad_id=historial.especialidad_id_colegio,
        por_presupuesto=bool(valor and valor.por_presupuesto),
        requiere_autorizacion=requiere_autorizacion_efectiva(valor, nomenclador),
        fecha_practica=fecha,
        precio_base=precio_base,
        precio_total=precio_total,
        componentes=componentes_out,
        via=via,
        nivel_cotizado=nivel_cotizado,
    )
