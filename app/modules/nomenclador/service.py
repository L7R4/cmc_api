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
# Cálculo de precio
# ─────────────────────────────────────────────────────────────────────────────

async def calcular_precio_total(
    valor_id: int, db: AsyncSession
) -> tuple[Decimal, list]:
    """
    Suma los componentes obligatorios de un Valor y devuelve
    (precio_total, componentes_snapshot).
    El snapshot incluye todos los componentes (obligatorios y opcionales).
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
                "concepto": comp.concepto,
                "tipo": "calculable",
                "galeno_id": comp.galeno_id,
                "galeno_codigo": galeno.codigo if galeno else None,
                "cantidad": str(comp.cantidad),
                "valor_unitario": str(precio_unidad),
                "subtotal": str(subtotal),
                "opcional": comp.opcional,
            })
        else:
            # fijo
            subtotal = comp.valor_unitario or Decimal("0")
            snapshot.append({
                "concepto": comp.concepto,
                "tipo": "fijo",
                "galeno_id": None,
                "galeno_codigo": None,
                "cantidad": str(comp.cantidad),
                "valor_unitario": str(subtotal),
                "subtotal": str(subtotal),
                "opcional": comp.opcional,
            })

        if not comp.opcional:
            precio_total += subtotal

    return precio_total, snapshot


# ─────────────────────────────────────────────────────────────────────────────
# Motor de historial — Regla B: nuevo valores (valor fijo o estructura)
# ─────────────────────────────────────────────────────────────────────────────

async def regenerar_historial_por_valores(
    nuevo_valor_id: int,
    fecha_corte: Optional[datetime.date],
    db: AsyncSession,
    motivo: str = "carga_inicial",
) -> None:
    """
    Regla B §28: cierra la fila vigente del historial para el mismo
    (nomenclador_id, obra_social_nro, convenio_id) e inserta una nueva.
    """
    nuevo_valor = await db.get(Valor, nuevo_valor_id)
    if not nuevo_valor:
        return

    precio_total, snapshot = await calcular_precio_total(nuevo_valor_id, db)

    # Cerrar fila vigente anterior (si existe y hay fecha de corte)
    if fecha_corte:
        await db.execute(
            update(HistorialPrecioCodigo)
            .where(
                HistorialPrecioCodigo.nomenclador_id == nuevo_valor.nomenclador_id,
                HistorialPrecioCodigo.obra_social_nro == nuevo_valor.obra_social_nro,
                HistorialPrecioCodigo.convenio_id == nuevo_valor.convenio_id,
                HistorialPrecioCodigo.vigencia_hasta.is_(None),
            )
            .values(vigencia_hasta=fecha_corte)
        )

    historial = HistorialPrecioCodigo(
        nomenclador_id=nuevo_valor.nomenclador_id,
        obra_social_nro=nuevo_valor.obra_social_nro,
        convenio_id=nuevo_valor.convenio_id,
        vigencia_desde=nuevo_valor.vigencia_desde,
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
    Regla A §28:
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
            valor_id, fecha_corte, db, motivo="galeno_actualizado"
        )
        # Actualizar la nueva fila con referencia al nuevo galeno
        # (ya fue seteada en regenerar_historial_por_valores con referencia_cambio_id=valor_id)
        # Reasignamos referencia al galeno nuevo
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
# Motor de historial — Regla C: nuevo convenio
# ─────────────────────────────────────────────────────────────────────────────

async def regenerar_historial_convenio_nuevo(
    convenio_id: int, db: AsyncSession
) -> int:
    """
    Regla C §28: genera filas de historial para todos los valores activos
    del nuevo convenio. Retorna la cantidad de filas insertadas.
    """
    stmt = select(Valor).where(
        Valor.convenio_id == convenio_id,
        Valor.estado == "activo",
    )
    result = await db.execute(stmt)
    valores = result.scalars().all()

    for valor in valores:
        await regenerar_historial_por_valores(
            valor.id, None, db, motivo="convenio_nuevo"
        )

    return len(valores)


# ─────────────────────────────────────────────────────────────────────────────
# Lookup de precio
# ─────────────────────────────────────────────────────────────────────────────

class LookupError(Exception):
    def __init__(self, message: str, status_code: int = 422):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


async def lookup_precio(
    nomenclador_id: int,
    obra_social_nro: int,
    fecha: datetime.date,
    medico_id: int,
    opcionales_activos: List[int],
    db: AsyncSession,
) -> LookupPrecioOut:
    """
    Lookup directo en historial_precio_codigo + validación de habilitación del médico.
    Lanza LookupError con el motivo si alguna validación falla.
    """
    today = datetime.date.today()

    # 1. Fecha futura
    if fecha > today:
        raise LookupError("No se permiten prestaciones con fecha futura")

    # 2. Más de 6 meses de atraso
    if fecha < today - datetime.timedelta(days=182):
        raise LookupError("Prestación con más de 6 meses de atraso; use modo manual")

    # 3-7. Validar habilitación del médico
    medico = await db.get(ListadoMedico, medico_id)
    if not medico:
        raise LookupError("Médico no encontrado", 404)

    # Excepciones específicas por médico (inhabilita gana sobre todo)
    stmt_inh = select(MedicoCodigoHabilitado).where(
        MedicoCodigoHabilitado.medico_id == medico_id,
        MedicoCodigoHabilitado.nomenclador_id == nomenclador_id,
        MedicoCodigoHabilitado.tipo == "inhabilita",
        MedicoCodigoHabilitado.activo == True,
        and_(
            (MedicoCodigoHabilitado.vigencia_desde.is_(None)) |
            (MedicoCodigoHabilitado.vigencia_desde <= fecha),
            (MedicoCodigoHabilitado.vigencia_hasta.is_(None)) |
            (MedicoCodigoHabilitado.vigencia_hasta >= fecha),
        ),
    )
    if (await db.execute(stmt_inh)).scalar_one_or_none():
        raise LookupError("Médico inhabilitado para este código")

    # Excepción positiva por médico
    stmt_hab = select(MedicoCodigoHabilitado).where(
        MedicoCodigoHabilitado.medico_id == medico_id,
        MedicoCodigoHabilitado.nomenclador_id == nomenclador_id,
        MedicoCodigoHabilitado.tipo == "habilita",
        MedicoCodigoHabilitado.activo == True,
        and_(
            (MedicoCodigoHabilitado.vigencia_desde.is_(None)) |
            (MedicoCodigoHabilitado.vigencia_desde <= fecha),
            (MedicoCodigoHabilitado.vigencia_hasta.is_(None)) |
            (MedicoCodigoHabilitado.vigencia_hasta >= fecha),
        ),
    )
    tiene_habilita = bool((await db.execute(stmt_hab)).scalar_one_or_none())

    if not tiene_habilita:
        # Verificar habilitación por especialidad
        nom = await db.get(NomencladorCMC, nomenclador_id)
        if not nom:
            raise LookupError("Código no encontrado en el nomenclador", 404)

        if not nom.sin_restriccion_especialidad:
            especialidad_medico = getattr(medico, "NRO_ESPECIALIDAD1", None)
            if especialidad_medico:
                stmt_esp = select(NomencladorEspecialidad).where(
                    NomencladorEspecialidad.nomenclador_id == nomenclador_id,
                    NomencladorEspecialidad.especialidad_id_colegio == especialidad_medico,
                    NomencladorEspecialidad.activo == True,
                )
                if not (await db.execute(stmt_esp)).scalar_one_or_none():
                    raise LookupError("Médico sin habilitación por especialidad para este código")
            else:
                raise LookupError("Médico sin habilitación para este código")

    # 8-9. Lookup en historial
    stmt_hist = select(HistorialPrecioCodigo).where(
        HistorialPrecioCodigo.nomenclador_id == nomenclador_id,
        HistorialPrecioCodigo.obra_social_nro == obra_social_nro,
        fecha >= HistorialPrecioCodigo.vigencia_desde,
        (HistorialPrecioCodigo.vigencia_hasta.is_(None)) |
        (HistorialPrecioCodigo.vigencia_hasta >= fecha),
    )
    historial = (await db.execute(stmt_hist)).scalar_one_or_none()
    if not historial:
        raise LookupError("Sin precio registrado para ese código, obra social y fecha")

    nom = nom if "nom" in dir() else await db.get(NomencladorCMC, nomenclador_id)
    valor = await db.get(Valor, historial.valores_id)

    # 10-12. Calcular con opcionales activados
    precio_base = historial.precio_total
    precio_total = precio_base
    componentes_out: List[ComponenteLookupOut] = []

    for item in historial.componentes_snapshot:
        incluido = not item["opcional"]
        componentes_out.append(ComponenteLookupOut(
            concepto=item["concepto"],
            tipo=item["tipo"],
            galeno_id=item.get("galeno_id"),
            galeno_codigo=item.get("galeno_codigo"),
            cantidad=Decimal(item["cantidad"]),
            valor_unitario=Decimal(item["valor_unitario"]),
            subtotal=Decimal(item["subtotal"]),
            opcional=item["opcional"],
            incluido=incluido,
        ))

    # Agregar opcionales activados
    for comp_id in opcionales_activos:
        comp = await db.get(ValorComponente, comp_id)
        if not comp or not comp.opcional or comp.valor_id != historial.valores_id:
            continue
        if comp.galeno_id:
            galeno = await db.get(Galeno, comp.galeno_id)
            precio_unidad = galeno.valor_unitario if galeno else Decimal("0")
            subtotal = comp.cantidad * precio_unidad
        else:
            subtotal = comp.valor_unitario or Decimal("0")
            precio_unidad = subtotal

        precio_total += subtotal
        # Actualizar el componente en la lista de salida como incluido
        for c in componentes_out:
            if c.galeno_id == comp.galeno_id and c.opcional:
                c.incluido = True
                break

    return LookupPrecioOut(
        nomenclador_id=nomenclador_id,
        codigo_colegio=nom.codigo if nom else "",
        descripcion=valor.descripcion if valor else None,
        obra_social_nro=obra_social_nro,
        convenio_id=historial.convenio_id,
        fecha_practica=fecha,
        precio_base=precio_base,
        precio_total=precio_total,
        componentes=componentes_out,
    )
