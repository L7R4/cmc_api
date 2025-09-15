# app/services/liquidaciones.py
from typing import Dict, Any, List, Optional, Set, Tuple
from decimal import Decimal
import re, datetime
from app.services.liquidaciones_calc import calcular_version_y_formatear_nro
from sqlalchemy import select, or_, and_, exists, func, case
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from app.db.models import DeduccionAplicacion, DeduccionColegio, Descuentos, GuardarAtencion, LiquidacionResumen, ObrasSociales, ListadoMedico, DetalleLiquidacion, Debito_Credito, DetalleLiquidacion, Liquidacion



# ==============================
# Helpers (nivel módulo)
# ==============================

_PERIODO_RX = re.compile(r"^\s*(\d{4})[-/](\d{1,2})\s*$")

def normalizar_periodo_flexible(periodo_id: str | int):
    s = str(periodo_id).strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", s)
    if m:
        anio = int(m.group(1)); mes = int(m.group(2))
        if 1 <= mes <= 12:
            return anio, mes, f"{anio:04d}-{mes:02d}"
    m = re.fullmatch(r"(\d{4})(\d{2})", s)
    if m:
        anio = int(m.group(1)); mes = int(m.group(2))
        if 1 <= mes <= 12:
            return anio, mes, f"{anio:04d}-{mes:02d}"
    raise HTTPException(400, "periodo_id inválido; use 'YYYY-MM' o 'YYYYMM'")

def separar_anio_mes(periodo_normalizado: str) -> Tuple[int, int]:
    """
    'YYYY-MM' -> (YYYY, MM)
    """
    anio_str, mes_str = periodo_normalizado.split("-")
    return int(anio_str), int(mes_str)

def periodo_desde_fecha(fecha: Optional[datetime.date | str]) -> Optional[str]:
    """
    date|str -> 'YYYY-MM' | None
    """
    if not fecha:
        return None
    if isinstance(fecha, datetime.date):
        return f"{fecha.year:04d}-{fecha.month:02d}"
    if isinstance(fecha, str) and len(fecha) >= 7:
        return fecha[:7]
    return None

def to_int_id(value: Any) -> Optional[int]:
    try:
        i = int(value)
        return i if i > 1 else None
    except (TypeError, ValueError):
        return None

def to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


async def _ajuste_por_dc(db: AsyncSession, debito_credito_id: Optional[int]) -> Decimal:
    """
    Devuelve el ajuste del DC con signo:
      - 'd' => -monto
      - 'c' => +monto
      - None o inexistente => 0
    """
    if not debito_credito_id:
        return Decimal("0")
    dc = await db.get(Debito_Credito, debito_credito_id)
    if not dc:
        return Decimal("0")
    monto = Decimal(str(dc.monto or 0))
    return monto if dc.tipo == "c" else (Decimal("0") - monto)

async def recomputar_pagado_detalle(db: AsyncSession, detalle_id: int) -> None:
    """
    Al cerrar:
      - Inicial (prev_detalle_id IS NULL):
          pagado = importe ± monto_DC_actual
      - Reliquidación (prev_detalle_id NOT NULL):
          pagado = pagado_del_detalle_anterior ± monto_DC_actual
      - Si no hay DC, el ajuste es 0.
    """
    det = await db.get(DetalleLiquidacion, detalle_id)
    if not det:
        raise HTTPException(404, "Detalle no encontrado")

    ajuste_actual = await _ajuste_por_dc(db, det.debito_credito_id)

    if det.prev_detalle_id:
        # base = lo ya pagado en la versión anterior (la anterior debió quedar cerrada)
        prev = await db.get(DetalleLiquidacion, det.prev_detalle_id)
        base = Decimal(str((prev.pagado if prev else 0) or 0))
    else:
        # inicial: base = importe actual
        base = Decimal(str(det.importe or 0))

    det.pagado = base + ajuste_actual
    await db.flush()
    
async def recomputar_totales_de_liquidacion(db: AsyncSession, liquidacion_id: int) -> None:
    """
    total_bruto = SUM(detalle.importe)
    total_debitos = SUM(monto de DC tipo 'd' ligados a detalles de esta liquidación)
    total_creditos = SUM(monto de DC tipo 'c' ligados a detalles de esta liquidación)
    total_neto = total_bruto - total_debitos + total_creditos
    """
    liq = await db.get(Liquidacion, liquidacion_id)
    if not liq:
        raise HTTPException(404, "Liquidación no encontrada")

    # SUM de importes por detalle
    q_bruto = await db.execute(
        select(func.coalesce(func.sum(DetalleLiquidacion.importe), 0))
        .where(DetalleLiquidacion.liquidacion_id == liquidacion_id)
    )
    total_bruto = Decimal(q_bruto.scalar_one() or 0)

    # SUM débito/crédito por join 1:1
    monto_case = case(
        (Debito_Credito.tipo == "d", -Debito_Credito.monto),
        (Debito_Credito.tipo == "c", Debito_Credito.monto),
        else_=Decimal("0")
    )
    q_dc = await db.execute(
        select(
            func.coalesce(func.sum(
                case((Debito_Credito.tipo == "d", Debito_Credito.monto), else_=Decimal("0"))
            ), 0).label("debitos"),
            func.coalesce(func.sum(
                case((Debito_Credito.tipo == "c", Debito_Credito.monto), else_=Decimal("0"))
            ), 0).label("creditos"),
        ).select_from(DetalleLiquidacion)
         .join(Debito_Credito, DetalleLiquidacion.debito_credito_id == Debito_Credito.id, isouter=True)
         .where(DetalleLiquidacion.liquidacion_id == liquidacion_id)
    )
    row = q_dc.first()
    total_debitos = Decimal(row.debitos or 0)
    total_creditos = Decimal(row.creditos or 0)

    liq.total_bruto = total_bruto
    liq.total_debitos = total_debitos
    liq.total_neto = total_bruto - total_debitos + total_creditos

    await db.flush()  

async def recomputar_pagados_de_liquidacion(db: AsyncSession, liquidacion_id: int) -> None:
    ids = (
        await db.execute(
            select(DetalleLiquidacion.id)
            .where(DetalleLiquidacion.liquidacion_id == liquidacion_id)
        )
    ).scalars().all()

    for det_id in ids:
        await recomputar_pagado_detalle(db, det_id)

    await db.flush()

async def recomputar_totales_de_resumen(db: AsyncSession, resumen_id: int) -> None:
    """
    Recalcula el resumen en base a la suma de TODAS las liquidaciones que cuelgan de ese resumen.
      - total_bruto   = SUM(liq.total_bruto)
      - total_debitos = SUM(liq.total_debitos)
      - total_deduccion: se deja como está (o se recalcula si tenés una tabla de deducciones del resumen)
      - total_neto    = total_bruto - (total_debitos + total_deduccion)   (si el campo existe)
    No hace commit; sólo flush.
    """
    # 1) traer el resumen
    resumen = await db.get(LiquidacionResumen, resumen_id)
    if not resumen:
        raise HTTPException(404, "LiquidacionResumen no encontrado")

    # 2) sumar bruto y débitos de TODAS las liquidaciones del resumen
    sums = await db.execute(
        select(
            func.coalesce(func.sum(Liquidacion.total_bruto), 0),
            func.coalesce(func.sum(Liquidacion.total_debitos), 0),
        ).where(Liquidacion.resumen_id == resumen_id)
    )
    bruto_sum, debitos_sum = sums.first() or (0, 0)
    total_bruto = Decimal(str(bruto_sum or 0))
    total_debitos = Decimal(str(debitos_sum or 0))

    # 3) deducciones del resumen:
    #    Opción A (por defecto): mantener lo que ya tiene cargado el resumen (no lo recalculamos acá)
    total_deduccion = Decimal(str(getattr(resumen, "total_deduccion", 0) or 0))

    #    Opción B (si llevás deducciones del resumen en otra tabla, descomentá y ajustá):
    # qd = await db.execute(
    #     select(func.coalesce(func.sum(DebitoColegio.monto), 0))
    #     .where(DebitoColegio.resumen_id == resumen_id)
    # )
    # total_deduccion = Decimal(str(qd.scalar_one() or 0))

    # 4) neto del resumen (si el modelo tiene el campo). Si no lo tiene, se ignora.
    total_neto = total_bruto - (total_debitos + total_deduccion)

    resumen.total_bruto = total_bruto
    resumen.total_debitos = total_debitos
    resumen.total_deduccion = total_deduccion

    await db.flush()

async def recomputar_todo_de_liquidacion(db: AsyncSession, liquidacion_id: int) -> None:
    # 1) fijar pagados (según reglas arriba)
    await recomputar_pagados_de_liquidacion(db, liquidacion_id)
    # 2) recalcular totales de la liquidación (bruto, débitos, créditos, neto)
    await recomputar_totales_de_liquidacion(db, liquidacion_id)
    
    liq = await db.get(Liquidacion, liquidacion_id)
    await recomputar_totales_de_resumen(db, int(liq.resumen_id))

def now_string() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def reabrir_liquidacion_creando_version(
    db: AsyncSession,
    liquidacion_id: int,
    nro_base: str,  # ej: "000123"
) -> Liquidacion:
    """Crea nueva versión (version+1) clonando detalles (prev_detalle_id=old.id, pagado=old.pagado)."""
    old = await db.get(Liquidacion, liquidacion_id)
    if not old:
        raise HTTPException(404, "Liquidación no encontrada")
    if old.estado != "C":
        raise HTTPException(409, "Solo se puede reabrir una liquidación cerrada")

    # calcular siguiente versión + nro formateado
    version, nro_fmt = await calcular_version_y_formatear_nro(
        db, old.obra_social_id, old.anio_periodo, old.mes_periodo, nro_base
    )

    new_liq = Liquidacion(
        resumen_id=old.resumen_id,
        obra_social_id=old.obra_social_id,
        mes_periodo=old.mes_periodo,
        anio_periodo=old.anio_periodo,
        version=version,
        nro_liquidacion=nro_fmt,
        estado="A",
        total_bruto=Decimal("0"),
        total_debitos=Decimal("0"),
        total_neto=Decimal("0"),
    )
    db.add(new_liq)
    await db.flush()  # new_liq.id

    # clonar detalles
    old_detalles = (await db.execute(
        select(DetalleLiquidacion).where(DetalleLiquidacion.liquidacion_id == old.id)
    )).scalars().all()

    for d in old_detalles:
        db.add(DetalleLiquidacion(
            liquidacion_id=new_liq.id,
            medico_id=d.medico_id,
            obra_social_id=d.obra_social_id,
            prestacion_id=d.prestacion_id,
            prev_detalle_id=d.id,             # << enlace a la versión previa
            importe=d.importe,
            debito_credito_id=None,           # arranca sin DC
            pagado=d.pagado,                  # carry para la regla de monto en la UI
        ))

    await db.flush()
    await recomputar_totales_de_liquidacion(db, new_liq.id)
    return new_liq

async def reabrir_liquidacion_simple(db: AsyncSession, liquidacion_id: int) -> Liquidacion:
    liq = await db.get(Liquidacion, liquidacion_id)
    if not liq:
        raise HTTPException(404, "Liquidación no encontrada")
    if liq.estado != "C":
        raise HTTPException(409, "Solo se puede reabrir una liquidación cerrada")
    liq.estado = "A"
    liq.cierre_timestamp = None
    await db.flush()
    await db.refresh(liq)
    return liq

async def _base_bruto_por_medico_en_resumen(db: AsyncSession, resumen_id: int) -> dict[int, Decimal]:
    """
    Devuelve {medico_id: SUM(importe)} considerando *solo* las liquidaciones del resumen.
    """
    q = await db.execute(
        select(DetalleLiquidacion.medico_id, func.coalesce(func.sum(DetalleLiquidacion.importe), 0))
        .select_from(DetalleLiquidacion)
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .where(Liquidacion.resumen_id == resumen_id)
        .group_by(DetalleLiquidacion.medico_id)
    )
    out: dict[int, Decimal] = {}
    for med_id, suma in q:
        out[int(med_id)] = Decimal(suma or 0)
    return out


async def recomputar_total_deduccion_resumen(db: AsyncSession, resumen_id: int) -> None:
    """
    total_deduccion del resumen = Σ(DeduccionAplicacion.aplicado) del mes.
    """
    res = await db.get(LiquidacionResumen, resumen_id)
    if not res:
        raise HTTPException(404, "Resumen no encontrado")

    from sqlalchemy import func
    qsum = await db.execute(
        select(func.coalesce(func.sum(DeduccionAplicacion.aplicado), 0))
        .where(DeduccionAplicacion.resumen_id == resumen_id)
    )
    res.total_deduccion = Decimal(qsum.scalar_one() or 0).quantize(Decimal("0.01"))
    await db.flush()