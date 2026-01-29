# app/services/liquidaciones.py
from typing import Dict, Any, List, Optional, Set, Tuple
from decimal import Decimal
import re, datetime
# from app.services.liquidaciones_calc import calcular_version_y_formatear_nro
from sqlalchemy import select, or_, and_, exists, func, case
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from app.db.models import DeduccionAplicacion, Deduccion, Descuentos, GuardarAtencion, LiquidacionResumen, ObrasSociales, ListadoMedico, DetalleLiquidacion, Debito_Credito, DetalleLiquidacion, Liquidacion



# ==============================
# Helpers (nivel módulo)
#region ==============================

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

#endregion

# ================================================
# Helper para devolver el ajuste segun si es debito o credito
# ================================================
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

# ================================================
# Helper para formatear nro de factura
# ================================================
async def _formatear_nro_factura(
    db: AsyncSession, punto_venta: str, nro_factura: str,
) -> str:
    
    punto_venta = f"{(punto_venta or '').strip()}"
    nro_factura = f"{(nro_factura or '').strip()}"
    return f"{(punto_venta or '').strip()}-{(nro_factura or '').strip()}"

    
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

    # 2) recalcular totales de la liquidación (bruto, débitos, créditos, neto)
    await recomputar_totales_de_liquidacion(db, liquidacion_id)
    
    liq = await db.get(Liquidacion, liquidacion_id)
    await recomputar_totales_de_resumen(db, int(liq.resumen_id))


def now_string() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ================================================
# Servicio para refacturar una liquidacion
# ================================================
async def refacturar_service(
    db: AsyncSession,
    liquidacion_id: int,
    punto_venta:str,
    nro_factura: str, 
) -> Liquidacion:
    
    old = await db.get(Liquidacion, liquidacion_id)
    if not old:
        raise HTTPException(404, "Liquidación no encontrada")
    if old.estado != "C":
        raise HTTPException(409, "Solo se puede reabrir una liquidación cerrada")

    # calcular siguiente versión + nro formateado
    n_factura = await _formatear_nro_factura(punto_venta,nro_factura)

    new_liq = Liquidacion(
        resumen_id=old.resumen_id,
        obra_social_id=old.obra_social_id,
        mes_periodo=old.mes_periodo,
        anio_periodo=old.anio_periodo,
        refacturado_from=old.id,
        nro_factura=n_factura,
        estado="A",
        total_bruto=Decimal("0"),
        total_debitos=Decimal("0"),
        total_neto=Decimal("0"),
    )
    db.add(new_liq)
    await db.flush()
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


# ================================================
# Recalcular los totales de un resumen de liquidación
# ================================================
async def recalcular_resumen_liquidacion(db: AsyncSession, resumen_id: int) -> Dict[str, Decimal]:
    """
    Recalcula y devuelve los totales del resumen:
      - total_bruto
      - total_debitos
      - total_deduccion
      - total_neto

    También actualiza los campos correspondientes en el registro `LiquidacionResumen`.
    """
    resumen = await db.get(LiquidacionResumen, resumen_id)
    if not resumen:
        raise HTTPException(404, "LiquidacionResumen no encontrado")

    sums = await db.execute(
        select(
            func.coalesce(func.sum(Liquidacion.total_bruto), 0).label("bruto"),
            func.coalesce(func.sum(Liquidacion.total_debitos), 0).label("debitos"),
        ).where(Liquidacion.resumen_id == resumen_id)
    )
    row = sums.first() or (0, 0)
    total_bruto = Decimal(str(row.bruto or 0)).quantize(Decimal("0.01"))
    total_debitos = Decimal(str(row.debitos or 0)).quantize(Decimal("0.01"))

    qd = await db.execute(
        select(func.coalesce(func.sum(DeduccionAplicacion.aplicado), 0))
        .where(
            func.extract('year', DeduccionAplicacion.created_at) == resumen.anio,
            func.extract('month', DeduccionAplicacion.created_at) == resumen.mes
        )
    )
    total_deduccion = Decimal(str(qd.scalar_one() or 0)).quantize(Decimal("0.01"))

    total_neto = (total_bruto - (total_debitos + total_deduccion)).quantize(Decimal("0.01"))

    return {
        "total_bruto": total_bruto,
        "total_debitos": total_debitos,
        "total_deduccion": total_deduccion,
        "total_neto": total_neto,
    }
