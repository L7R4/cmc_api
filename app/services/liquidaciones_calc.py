# services/liquidaciones_v2.py

from sqlalchemy import select, func, and_, or_, literal, String, cast,case
from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
import re

from app.db.models import (
    Liquidacion, LiquidacionResumen, DetalleLiquidacion, Debito_Credito, GuardarAtencion
)

def period_str(anio: int, mes: int) -> str:
    return f"{int(anio):04d}-{int(mes):02d}"

def to_dec(x) -> Decimal:
    try:
        return Decimal(str(x or "0")).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0")

# -------- 1) Desdoblar una fila de guardar_atencion en piezas por actor --------
def desdoblar_en_actores(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    row: mapping con las columnas de GuardarAtencion ya seleccionadas.
    Devuelve piezas: {prestacion_id, medico_id, importe}
    """
    piezas: List[Dict[str, Any]] = []
    factor = int(row.get("cantidad") or 1) * int(row.get("cantidad_tratamiento") or 1)

    id_atencion = row["id_atencion"]

    # Médico principal
    medico_id = row.get("medico_id")
    if medico_id:
        bruto = to_dec(row.get("valor_cirugia")) * factor
        if bruto > 0:
            piezas.append({
                "prestacion_id": str(id_atencion),
                "medico_id": int(medico_id),
                "importe": bruto,
            })

    # Ayudante 1
    ayud1 = row.get("nro_socio_ayudante")
    if ayud1:
        imp = to_dec(row.get("valor_ayudante"))
        # si querés multiplicar por factor, cambiá la línea siguiente:
        # imp = to_dec(row.get("valor_ayudante")) * factor
        if imp > 0:
            piezas.append({
                "prestacion_id": str(id_atencion),
                "medico_id": int(ayud1),
                "importe": imp,
            })

    # Ayudante 2
    ayud2 = row.get("nro_socio_ayudante_2")
    if ayud2:
        imp = to_dec(row.get("valor_ayudante_2"))
        # o * factor si corresponde
        if imp > 0:
            piezas.append({
                "prestacion_id": str(id_atencion),
                "medico_id": int(ayud2),
                "importe": imp,
            })

    return piezas

# -------- 2) Calcular versión y formatear nro_liquidacion --------
async def calcular_version_y_formatear_nro(
    db: AsyncSession, obra_social_id: int, anio: int, mes: int, nro_base: str
) -> Tuple[int, str]:
    q = await db.execute(
        select(func.count(Liquidacion.id)).where(
            Liquidacion.obra_social_id == obra_social_id,
            Liquidacion.anio_periodo == anio,
            Liquidacion.mes_periodo == mes,
        )
    )
    version = int(q.scalar_one() or 0)
    nro_fmt = f"{version:03d}-{(nro_base or '').strip()}"
    return version, nro_fmt

# -------- 3) Construir detalles y actualizar totales --------
async def construir_detalles_y_totales(db: AsyncSession, liquidacion_id: int) -> None:
    liq = (await db.execute(select(Liquidacion).where(Liquidacion.id == liquidacion_id))).scalars().first()
    if not liq:
        return

    anio, mes = int(liq.anio_periodo), int(liq.mes_periodo)
    os_id = int(liq.obra_social_id)
    periodo = period_str(anio, mes)

    # traer atenciones del periodo
    rows = (await db.execute(
        select(
            GuardarAtencion.ID.label("id_atencion"),
            GuardarAtencion.NRO_SOCIO.label("medico_id"),
            GuardarAtencion.NRO_OBRA_SOCIAL.label("obra_social_id"),
            GuardarAtencion.CODIGO_PRESTACION.label("codigo_prestacion"),
            GuardarAtencion.FECHA_PRESTACION.label("fecha_prestacion"),
            GuardarAtencion.VALOR_CIRUJIA.label("valor_cirugia"),
            GuardarAtencion.VALOR_AYUDANTE.label("valor_ayudante"),
            GuardarAtencion.VALOR_AYUDANTE_2.label("valor_ayudante_2"),
            GuardarAtencion.GASTOS.label("gastos"),
            GuardarAtencion.CANTIDAD.label("cantidad"),
            GuardarAtencion.CANT_TRATAMIENTO.label("cantidad_tratamiento"),
            GuardarAtencion.AYUDANTE.label("nro_socio_ayudante"),
            GuardarAtencion.AYUDANTE_2.label("nro_socio_ayudante_2"),
        ).where(
            GuardarAtencion.NRO_OBRA_SOCIAL == os_id,
            GuardarAtencion.ANIO_PERIODO == anio,
            GuardarAtencion.MES_PERIODO == mes,
            GuardarAtencion.EXISTE == "S",
        )
    )).mappings().all()

    # mapa para buscar el último detalle anterior por prestacion_id
    prev_detalle_by_prest: Dict[str, int] = {}

    if liq.version > 0:
        # buscar el último detalle anterior (máxima version < liq.version) para mismas prestaciones
        # primero obtengo ids de liquidaciones previas para el mismo OS+periodo
        prev_liq_ids = (await db.execute(
            select(Liquidacion.id).where(
                Liquidacion.obra_social_id == os_id,
                Liquidacion.anio_periodo == anio,
                Liquidacion.mes_periodo == mes,
                Liquidacion.version < liq.version
            )
        )).scalars().all()

        if prev_liq_ids:
            prev_detalles = (await db.execute(
                select(DetalleLiquidacion.prestacion_id, func.max(DetalleLiquidacion.id))
                .where(DetalleLiquidacion.liquidacion_id.in_(prev_liq_ids))
                .group_by(DetalleLiquidacion.prestacion_id)
            )).all()
            prev_detalle_by_prest = {str(p): int(did) for (p, did) in prev_detalles}

    total_bruto = Decimal("0")

    for r in rows:
        piezas = desdoblar_en_actores(dict(r))
        for p in piezas:
            prev_id = prev_detalle_by_prest.get(p["prestacion_id"])
            det = DetalleLiquidacion(
                liquidacion_id=liq.id,
                medico_id=p["medico_id"],
                obra_social_id=os_id,
                prestacion_id=p["prestacion_id"],
                prev_detalle_id=prev_id,
                importe=p["importe"],
            )
            db.add(det)
            total_bruto += p["importe"]

    await db.flush()

    # Débitos/Créditos del periodo actual para las atenciones incluidas
    atencion_ids = [str(r["id_atencion"]) for r in rows]
    sum_debitos = Decimal("0")
    sum_creditos = Decimal("0")
    if atencion_ids:
        dc = (await db.execute(
            select(Debito_Credito.tipo, func.sum(Debito_Credito.monto))
            .where(
                Debito_Credito.obra_social_id == os_id,
                Debito_Credito.periodo == periodo,
                Debito_Credito.id_atencion.in_(atencion_ids),
            )
            .group_by(Debito_Credito.tipo)
        )).all()
        for tipo, total in dc:
            if tipo == "d":
                sum_debitos += to_dec(total)
            else:
                sum_creditos += to_dec(total)

    liq.total_bruto = total_bruto
    liq.total_debitos = sum_debitos   # (separado de créditos)
    liq.total_neto = total_bruto - sum_debitos + sum_creditos

    await db.commit()

# -------- 4) Vista de filas para InsuranceDetail --------
async def vista_detalles_liquidacion(
    db: AsyncSession,
    liquidacion_id: int,
    medico_id: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    DL, GA, DC = DetalleLiquidacion, GuardarAtencion, Debito_Credito

    NRO_AFILIADO = getattr(GA, "NRO_AFILIADO", literal(""))
    NOMBRE_AFILIADO = getattr(GA, "NOMBRE_AFILIADO", literal(""))
    MATRICULA = getattr(GA, "MATRICULA", GA.NRO_SOCIO)

    importe_col = func.coalesce(DL.importe, 0).label("importe")
    tipo_ui_col = case((DC.tipo == "d", literal("D")),
                       (DC.tipo == "c", literal("C")),
                       else_=literal("N")).label("tipo")
    monto_ui_col = case(
        (DC.id.isnot(None), func.coalesce(DC.monto, 0)),
        (DL.prev_detalle_id.is_(None), func.coalesce(DL.importe, 0)),
        else_=func.abs(func.coalesce(DL.pagado, 0)),
    ).label("monto")

    stmt = (
        select(
            DL.id.label("det_id"),
            DL.medico_id.label("socio"),
            GA.NOMBRE_PRESTADOR.label("nombreSocio"),
            MATRICULA.label("matri"),
            DL.prestacion_id.label("nroOrden"),
            GA.FECHA_PRESTACION.label("fecha"),
            GA.CODIGO_PRESTACION.label("codigo"),
            NRO_AFILIADO.label("nroAfiliado"),
            NOMBRE_AFILIADO.label("afiliado"),
            GA.CANTIDAD.label("cantidad"),
            GA.CANT_TRATAMIENTO.label("cantidad_tratamiento"),
            GA.PORCENTAJE.label("porcentaje"),
            GA.VALOR_CIRUJIA.label("honorarios"),
            GA.GASTOS.label("gastos"),
            literal(0).label("coseguro"),
            importe_col,
            literal(0).label("pagado"),
            DC.tipo.label("tipo_dc"),
            DC.monto.label("monto_dc"),
            DC.observacion.label("obs_dc"),
            tipo_ui_col,
            monto_ui_col,
        )
        .select_from(DL)
        .join(GA, DL.prestacion_id == cast(GA.ID, String(16)), isouter=True)
        .join(DC, DL.debito_credito_id == DC.id, isouter=True)
        .where(DL.liquidacion_id == liquidacion_id)
        .order_by(DL.id)
    )
    if medico_id is not None:
        stmt = stmt.where(DL.medico_id == medico_id)

    rows = (await db.execute(stmt)).mappings().all()

    out: List[Dict[str, Any]] = []
    for r in rows:
        importe = Decimal(str(r["importe"] or "0"))
        tipo = (r["tipo"] or "N").upper()
        monto = Decimal(str(r["monto"] or "0"))
        total = importe - monto if tipo == "D" else importe + monto if tipo == "C" else importe
        xCant = f'{int(r.get("cantidad") or 1)}-{int(r.get("cantidad_tratamiento") or 1)}'

        out.append({
            "det_id": r["det_id"],
            "socio": r["socio"],
            "nombreSocio": (r["nombreSocio"] or "").strip(),
            "matri": r["matri"],
            "nroOrden": r["nroOrden"],
            "fecha": str(r["fecha"]) if r["fecha"] is not None else "",
            "codigo": r["codigo"],
            "nroAfiliado": r.get("nroAfiliado") or "",
            "afiliado": r.get("afiliado") or "",
            "xCant": xCant,
            "porcentaje": float(r["porcentaje"] or 0),
            "honorarios": float(r["honorarios"] or 0),
            "gastos": float(r["gastos"] or 0),
            "coseguro": 0.0,
            "importe": float(importe),
            "pagado": 0.0,
            "tipo": tipo,
            "monto": float(monto),
            "obs": r.get("obs_dc") or None,
            "total": float(total),
        })
    return out, len(out)

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


def _dec(v) -> float:
    from decimal import Decimal as D
    if v is None:
        return 0.0
    if isinstance(v, D):
        return float(v)
    return float(v)

def _is_refacturacion(liq) -> bool:
    """
    Considera refacturación si el índice de facturación != '000'.
    Si el campo llega con otros formatos, intenta tomar los últimos 3 dígitos.
    """
    raw = (liq.nro_liquidacion or "").strip()
    
    idx = re.match(r'^\s*(\d{3})(?=\s*[-/])', raw).group(1)
    
    return idx != "000"

async def _calc_row_total(db: AsyncSession, det, liq) -> float:
    """
    Para la UI (columna 'Total'):
      - si es refacturación => base = det.pagado
      - si es factura inicial => base = det.importe
    y luego aplica el ajuste del DC (± monto).
    """
    # Si querés asegurarte de que 'pagado' esté alineado en refacturación:
    # if _is_refacturacion(liq):
    #     await recomputar_pagado_detalle(db, det.id)  # opcional
    ajuste = await _ajuste_por_dc(db, det.debito_credito_id)
    if _is_refacturacion(liq):
        print("WEpsss")
        base = Decimal(str(det.pagado or 0))
    else:
        print("WEpsss2")
        base = Decimal(str(det.importe or 0))
    return _dec(base + ajuste)