# app/services/liquidaciones_colegio.py
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Tuple

from sqlalchemy import select, func, or_, cast, BigInteger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.db.models import (
    LiquidacionResumen,
    DeduccionColegio,      # snapshot del mes (resumen_id, medico_id, descuento_id, monto, porcentaje)
    DeduccionSaldo,        # saldo acumulado por (concepto_tipo='desc', concepto_id, medico_id, saldo)
    SocioDescuento,        # NUEVO: (medico_id, descuento_id, fecha_alta, fecha_baja)
    Descuentos,             # NUEVO: (id, nombre, nro_colegio, precio, porcentaje, ...)
    GuardarAtencion,       # para calcular bruto del período por médico
)

TWOPLACES = Decimal("0.01")

async def _get_descuento(db: AsyncSession, desc_id: int) -> Descuentos:
    obj = await db.scalar(select(Descuentos).where(Descuentos.id == desc_id))
    if not obj:
        raise ValueError("Descuentos inexistente")
    return obj

async def _medicos_para_descuento(db: AsyncSession, desc_id: int) -> List[int]:
    """
    Devuelve los medico_id habilitados para este Descuentos (fecha_baja NULL o futura).
    """
    res = await db.execute(
        select(SocioDescuento.medico_id)
        .where(
            SocioDescuento.descuento_id == desc_id,
            or_(SocioDescuento.fecha_baja.is_(None), SocioDescuento.fecha_baja > func.current_date()),
        )
    )
    # set() para evitar duplicados
    return list({m for (m,) in res.all()})

async def _base_bruto_por_medico_en_resumen(db: AsyncSession, resumen_id: int) -> Dict[int, Decimal]:
    """
    Suma bruto del período por médico usando guardar_atencion (ajustá columnas si tu modelo difiere).
    """
    # Relación: detalle_liquidacion -> guardar_atencion(ID) ya la usás en otros lados; acá tomamos directo del origen.
    # Si ya tenés una vista/consulta probada para el bruto por médico y resumen, usala acá.
    res = await db.execute(
        select(
            GuardarAtencion.MEDICO_ID.label("medico_id"),
            func.sum(GuardarAtencion.IMPORTE_BRUTO).label("bruto"),
        )
        .where(GuardarAtencion.RESUMEN_ID == resumen_id)
        .group_by(GuardarAtencion.MEDICO_ID)
    )
    out: Dict[int, Decimal] = {}
    for medico_id, bruto in res.all():
        out[int(medico_id)] = (Decimal(bruto or 0)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    return out

def _calc_monto(p_base: Decimal, precio: Decimal | None, porcentaje: Decimal | None) -> Tuple[Decimal, Decimal | None]:
    """
    Si porcentaje > 0 => monto = base * %.
    Sino, si precio > 0 => monto = precio.
    Sino => 0.
    Devuelve (monto, porcentaje_usado|None)
    """
    if porcentaje and porcentaje > 0:
        m = (p_base * porcentaje / Decimal(100)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
        return (m, porcentaje)
    if precio and precio > 0:
        return (precio.quantize(TWOPLACES, rounding=ROUND_HALF_UP), None)
    return (Decimal(0), None)
