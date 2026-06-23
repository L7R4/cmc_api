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
