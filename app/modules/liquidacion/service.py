# app/services/liquidaciones.py
from typing import Dict, Any, List, Optional, Set, Tuple
from decimal import Decimal
import re, datetime
# from app.services.liquidaciones_calc import calcular_version_y_formatear_nro
from sqlalchemy import BigInteger, cast, literal, select, or_, and_, exists, func, case
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException
from sqlalchemy.orm import aliased


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

def to_dec(x) -> Decimal:
    try:
        return Decimal(str(x or "0")).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0")

def period_str(anio: int, mes: int) -> str:
    return f"{int(anio):04d}-{int(mes):02d}"


def _dec(v) -> float:
    from decimal import Decimal as D
    if v is None:
        return 0.0
    if isinstance(v, D):
        return float(v)
    return float(v)

def now_string() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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

def _is_refacturacion(liq) -> bool:
    """
    Considera refacturación si el índice de facturación != '000'.
    Si el campo llega con otros formatos, intenta tomar los últimos 3 dígitos.
    """
    raw = (liq.nro_factura or "").strip()
    
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

# ================================================
# Helper para dividir una prestacion por actores
# ================================================
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


# ================================================
# Servicio para generar los detalles de una liquidacion
# ================================================
async def build_detalles_liquidacion(db: AsyncSession, liquidacion_id: int) -> None:
    liq = (await db.execute(select(Liquidacion).where(Liquidacion.id == liquidacion_id))).scalars().first()
    if not liq:
        return

    anio, mes, os_id = int(liq.anio_periodo), int(liq.mes_periodo), int(liq.obra_social_id)
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

    for r in rows:
        piezas = desdoblar_en_actores(dict(r))
        for p in piezas:
            detalle_item = DetalleLiquidacion(
                liquidacion_id=liq.id,
                medico_id=p["medico_id"],
                obra_social_id=os_id,
                prestacion_id=p["prestacion_id"],
                pagado = 0,
                importe=p["importe"],
            )
            db.add(detalle_item)

    await db.flush()
    await db.commit()


# ================================================
# Servicio para generar la vista de detalles de liquidacion
# ================================================
async def vista_detalles_liquidacion(
    db: AsyncSession,
    liquidacion_id: int,
    medico_id: Optional[int] = None,
    search: Optional[str] = None,  
) -> Tuple[List[Dict[str, Any]], int]:
    DL, GA, DC = DetalleLiquidacion, GuardarAtencion, Debito_Credito
    LM = aliased(ListadoMedico)

    # Campos opcionales (si no existen en GA, devolvemos vacío)
    NRO_AFILIADO = getattr(GA, "NRO_AFILIADO", literal(""))
    NOMBRE_AFILIADO = getattr(GA, "NOMBRE_AFILIADO", literal(""))
    MATRICULA = getattr(GA, "MATRICULA", GA.NRO_SOCIO)

    NOMBRE_SOCIO = func.coalesce(GA.NOMBRE_PRESTADOR, LM.NOMBRE).label("nombreSocio")

    filters = [DL.liquidacion_id == liquidacion_id]

    if medico_id is not None:
        filters.append(DL.medico_id == medico_id)

    if search:
        s = search.strip()
        if s:
            if s.isdigit():
                n = int(s)
                filters.append(
                    or_(
                        LM.NRO_SOCIO == n,              # listado_medico.NRO_SOCIO
                        DL.medico_id == n,              # por si querés que también matchee directo
                        GA.CODIGO_PRESTACION == s,      # guardar_atencion.CODIGO_PRESTACION exacto
                    )
                )
            else:
                like = f"%{s}%"
                filters.append(
                    or_(
                        LM.NOMBRE.like(like),               # listado_medico.NOMBRE contiene
                        GA.CODIGO_PRESTACION.like(like),    # guardar_atencion.CODIGO_PRESTACION contiene
                    )
                )

    stmt_base = (
        select(
            DL.id.label("det_id"),
            DL.medico_id.label("socio"),
            NOMBRE_SOCIO,
            MATRICULA.label("matri"),
            DL.prestacion_id.label("nroOrden"),
            GA.ID.label("atencion_id"),
            GA.FECHA_PRESTACION.label("fecha"),
            GA.CODIGO_PRESTACION.label("codigo"),
            NRO_AFILIADO.label("nroAfiliado"),
            NOMBRE_AFILIADO.label("afiliado"),
            GA.CANTIDAD.label("cantidad"),
            GA.CANT_TRATAMIENTO.label("cantidad_tratamiento"),
            GA.PORCENTAJE.label("porcentaje"),
            GA.VALOR_CIRUJIA.label("honorarios"),
            GA.GASTOS.label("gastos"),
            func.coalesce(DL.importe, 0).label("importe"),
            func.coalesce(DL.pagado, 0).label("pagado"),
            DL.obra_social_id.label("obra_social_id"),
        )
        .select_from(DL)
        .outerjoin(
            GA,
            cast(DL.prestacion_id, BigInteger) == GA.ID,
        )
        .outerjoin(
            LM,
            LM.NRO_SOCIO == DL.medico_id,
        )
        .where(and_(*filters))
        .order_by(DL.id)
    )

    base_rows = (await db.execute(stmt_base)).mappings().all()
    if not base_rows:
        return [], 0


    atencion_ids = {int(r["atencion_id"]) for r in base_rows if r["atencion_id"] is not None}
    os_ids = {int(r["obra_social_id"]) for r in base_rows if r["obra_social_id"] is not None}

    dc_map: dict[int, list[dict[str, Any]]] = {}
    if atencion_ids:
        stmt_dc = (
            select(
                DC.id_atencion.label("atencion_id"),
                DC.tipo.label("tipo"),
                DC.monto.label("monto"),
                DC.observacion.label("obs"),
                DC.obra_social_id.label("obra_social_id"),
            )
            .where(DC.id_atencion.in_(atencion_ids))
            .order_by(DC.id)
        )

        if os_ids:
            stmt_dc = stmt_dc.where(DC.obra_social_id.in_(os_ids))

        dc_rows = (await db.execute(stmt_dc)).mappings().all()

        for d in dc_rows:
            aid = int(d["atencion_id"])
            tipo = (d["tipo"] or "").lower()
            tipo_ui = "D" if tipo == "d" else "C" if tipo == "c" else None
            if not tipo_ui:
                continue

            dc_map.setdefault(aid, []).append({
                "tipo": tipo_ui,
                "monto": float(Decimal(str(d["monto"] or "0"))),
                "obs": (d["obs"] or None),
            })


    out: List[Dict[str, Any]] = []
    for r in base_rows:
        importe = Decimal(str(r["importe"] or "0"))
        pagado = Decimal(str(r["pagado"] or "0"))

        cantidad = int(r.get("cantidad") or 1)
        cant_trat = int(r.get("cantidad_tratamiento") or 1)
        xCant = f"{cantidad}-{cant_trat}"

        aid = int(r["atencion_id"]) if r["atencion_id"] is not None else None
        dc_list = dc_map.get(aid, []) if aid is not None else []

        sum_c = Decimal("0")
        sum_d = Decimal("0")
        for dc in dc_list:
            m = Decimal(str(dc["monto"] or 0))
            if dc["tipo"] == "C":
                sum_c += m
            elif dc["tipo"] == "D":
                sum_d += m

        total = importe + sum_c - sum_d

        out.append({
            "det_id": int(r["det_id"]),
            "socio": r["socio"],
            "nombreSocio": (r["nombreSocio"] or "").strip(),
            "matri": r["matri"],
            "nroOrden": r["nroOrden"],
            "fecha": str(r["fecha"]) if r["fecha"] is not None else "",
            "codigo": r["codigo"] if r["codigo"] is not None else "",
            "nroAfiliado": (r.get("nroAfiliado") or None),
            "afiliado": (r.get("afiliado") or None),
            "xCant": xCant,
            "porcentaje": float(r["porcentaje"] or 0),
            "honorarios": float(r["honorarios"] or 0),
            "gastos": float(r["gastos"] or 0),
            "coseguro": 0.0,
            "importe": float(importe),
            "pagado": float(pagado),
            "debitos_creditos_list": dc_list,
            "total": float(total),
        })

    return out, len(out)


# ================================================
# Recalcular los totales una liquidacion de una de liquidación obra social
# ================================================
async def recalcular_totales_de_liquidacion(db: AsyncSession, liquidacion_id: int) -> None:
    liq = (await db.execute(select(Liquidacion).where(Liquidacion.id == liquidacion_id))).scalars().first()
    if not liq:
        return

    detalles = (await db.execute(
        select(DetalleLiquidacion)
        .where(DetalleLiquidacion.liquidacion_id == liquidacion_id)
    )).scalars().all()

    total_bruto = sum((to_dec(d.importe) for d in detalles), Decimal("0"))

    # Débitos/Créditos del periodo actual para las atenciones incluidas
    atencion_ids = [d.prestacion_id for d in detalles]
    anio, mes, os_id = int(liq.anio_periodo), int(liq.mes_periodo), int(liq.obra_social_id)
    periodo = period_str(anio, mes)

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

