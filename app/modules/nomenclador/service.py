"""
Motor de generación y regeneración de historial_precio_codigo.

Todas las funciones públicas corren DENTRO de la transacción del caller.
Si cualquier paso falla, el rollback se propaga al caller automáticamente.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

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
            subtotal = comp.cantidad * precio_unidad
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
            subtotal = comp.valor_unitario or Decimal("0")
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

    return precio_total, snapshot


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
                        "motivo": "mezclaría galenos nivelados y sin nivel en el destino",
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
    def __init__(self, message: str, status_code: int = 422):
        self.message = message
        self.status_code = status_code
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
) -> None:
    """
    Gate de habilitación (¿puede hacer la práctica?). El precio que cobra lo
    deciden después las variantes de Valor.

    Orden de evaluación:
    1. inhabilita vigente                  → rechazar
    2. habilita vigente                    → permitir
    3. nomenclador.sin_restriccion = True  → permitir
    4. nomenclador_especialidad activo para alguna especialidad del médico → permitir
    5. ninguna                             → rechazar
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

    stmt_esp = select(NomencladorEspecialidad.id).where(
        NomencladorEspecialidad.nomenclador_id == nomenclador.id,
        NomencladorEspecialidad.especialidad_id_colegio.in_(especialidades),
        NomencladorEspecialidad.activo == True,
    )
    if not (await db.execute(stmt_esp)).first():
        raise LookupError("Médico sin habilitación por especialidad para este código")


async def lookup_precio(
    nomenclador_id: int,
    obra_social_nro: int,
    fecha: datetime.date,
    medico_id: int,
    db: AsyncSession,
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
    await _validar_habilitacion_medico(db, medico, nomenclador, fecha)

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
        raise LookupError("Sin precio registrado para ese código, obra social y fecha")

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
            "una especialidad que no posee y no hay variante sin especialidad"
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

    # Todos los componentes suman (ya no hay opcionales): precio_base == precio_total.
    precio_total = historial.precio_total
    precio_base = precio_total
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
            subtotal=Decimal(item["subtotal"]),
        )
        for item in historial.componentes_snapshot
    ]

    return LookupPrecioOut(
        nomenclador_id=nomenclador_id,
        codigo_colegio=nomenclador.codigo,
        descripcion=valor.descripcion if valor else None,
        obra_social_nro=obra_social_nro,
        nivel=valor.nivel if valor else None,
        origen=historial.origen,
        variante_especialidad_id=historial.especialidad_id_colegio,
        por_presupuesto=bool(valor and valor.por_presupuesto),
        fecha_practica=fecha,
        precio_base=precio_base,
        precio_total=precio_total,
        componentes=componentes_out,
    )
