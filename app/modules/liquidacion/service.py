# app/modules/liquidacion/service.py
from typing import Dict, Any, List, Optional, Set, Tuple
from decimal import Decimal
import datetime
import re
from sqlalchemy import func, select, or_, and_, literal
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException
from sqlalchemy.orm import aliased

from app.db.models import (
    DeduccionAplicacion, Deduccion,
    GuardarAtencion, LiquidacionResumen,
    ListadoMedico, DetalleLiquidacion, Debito_Credito,
    Liquidacion, LiquidacionMedico, Recibo, ReciboItem,
)


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
    """'YYYY-MM' -> (YYYY, MM)"""
    anio_str, mes_str = periodo_normalizado.split("-")
    return int(anio_str), int(mes_str)


def periodo_desde_fecha(fecha: Optional[datetime.date | str]) -> Optional[str]:
    """date|str -> 'YYYY-MM' | None"""
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
    if v is None:
        return 0.0
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def now_datetime() -> datetime.datetime:
    return datetime.datetime.now()


# Mantener now_string() por compatibilidad legacy (si algo lo importa)
def now_string() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ================================================
# Helper: determinar si es refacturación
# AGENT DO IT: usar refacturado_from en lugar de regex frágil sobre nro_factura
# ================================================
def _is_refacturacion(liq: Liquidacion) -> bool:
    return bool(liq.refacturado_from)


# ================================================
# Helper: formatear número de factura
# ================================================
def _formatear_nro_factura(punto_venta: str, nro_factura: str) -> str:
    return f"{(punto_venta or '').strip()}-{(nro_factura or '').strip()}"


# ================================================
# Helper: calcular total de fila considerando todos los DCs
# ================================================
async def _calc_row_total(db: AsyncSession, det: DetalleLiquidacion, liq: Liquidacion) -> float:
    """
    Total de la fila = base ± ajustes de TODOS los DCs asociados.
    - Si es refacturación: base = det.pagado
    - Si es liquidación inicial: base = det.importe
    Ajuste: creditos - debitos
    """
    dcs = (await db.execute(
        select(Debito_Credito.tipo, Debito_Credito.monto)
        .where(Debito_Credito.detalle_liquidacion_id == det.id)
    )).all()

    sum_c = sum(Decimal(str(r.monto or 0)) for r in dcs if r.tipo == "c")
    sum_d = sum(Decimal(str(r.monto or 0)) for r in dcs if r.tipo == "d")

    if _is_refacturacion(liq):
        base = Decimal(str(det.pagado or 0))
    else:
        base = Decimal(str(det.importe or 0))

    return _dec(base + sum_c - sum_d)


# ================================================
# Helper: desdoblar una prestación en actores
# ================================================
def desdoblar_en_actores(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    row: mapping con columnas de GuardarAtencion.
    Devuelve piezas: {prestacion_id (int), medico_id, importe}
    """
    piezas: List[Dict[str, Any]] = []
    factor = int(row.get("cantidad") or 1) * int(row.get("cantidad_tratamiento") or 1)

    id_atencion = int(row["id_atencion"])  # prestacion_id ahora es Integer

    # Médico principal (cirujano)
    medico_id = row.get("medico_id")
    if medico_id:
        bruto = to_dec(row.get("valor_cirugia")) * factor
        if bruto > 0:
            piezas.append({
                "prestacion_id": id_atencion,
                "medico_id": int(medico_id),
                "importe": bruto,
            })

    # Ayudante 1
    ayud1 = row.get("nro_socio_ayudante")
    if ayud1:
        imp = to_dec(row.get("valor_ayudante"))
        if imp > 0:
            piezas.append({
                "prestacion_id": id_atencion,
                "medico_id": int(ayud1),
                "importe": imp,
            })

    # Ayudante 2
    ayud2 = row.get("nro_socio_ayudante_2")
    if ayud2:
        imp = to_dec(row.get("valor_ayudante_2"))
        if imp > 0:
            piezas.append({
                "prestacion_id": id_atencion,
                "medico_id": int(ayud2),
                "importe": imp,
            })

    return piezas


# ================================================
# Reabrir liquidación (utilitario)
# ================================================
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


# ================================================
# Servicio de refacturación
# ================================================
async def refacturar_service(
    db: AsyncSession,
    liquidacion_id: int,
    punto_venta: str,
    nro_factura: str,
) -> Liquidacion:
    old = await db.get(Liquidacion, liquidacion_id)
    if not old:
        raise HTTPException(404, "Liquidación no encontrada")
    if old.estado != "C":
        raise HTTPException(409, "Solo se puede refacturar una liquidación cerrada")

    # AGENT DO IT: _formatear_nro_factura ya no requiere db
    n_factura = _formatear_nro_factura(punto_venta, nro_factura)

    new_liq = Liquidacion(
        resumen_id=old.resumen_id,
        obra_social_id=old.obra_social_id,
        mes_periodo=old.mes_periodo,
        anio_periodo=old.anio_periodo,
        refacturado_from=old.id,
        nro_factura=n_factura,
        estado="A",
        cierre_timestamp=None,
        total_bruto=Decimal("0"),
        total_debitos=Decimal("0"),
        total_neto=Decimal("0"),
    )
    db.add(new_liq)
    await db.flush()
    return new_liq


# ================================================
# Poblar detalles de liquidación desde guardar_atencion
# ================================================
async def build_detalles_liquidacion(db: AsyncSession, liquidacion_id: int) -> None:
    liq = (await db.execute(
        select(Liquidacion).where(Liquidacion.id == liquidacion_id)
    )).scalars().first()
    if not liq:
        return

    anio, mes, os_id = int(liq.anio_periodo), int(liq.mes_periodo), int(liq.obra_social_id)

    # Traer atenciones del periodo (EXISTE='S' excluye eliminadas)
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

    # Obtener prestacion_ids ya cargados para idempotencia
    existing = set((await db.execute(
        select(DetalleLiquidacion.prestacion_id, DetalleLiquidacion.medico_id)
        .where(DetalleLiquidacion.liquidacion_id == liquidacion_id)
    )).all())

    observados: list[dict] = []

    for r in rows:
        piezas = desdoblar_en_actores(dict(r))
        for p in piezas:
            key = (p["prestacion_id"], p["medico_id"])
            if key in existing:
                continue  # idempotencia

            # Validar importe
            if not p["importe"] or p["importe"] <= 0:
                observados.append({
                    "atencion_id": p["prestacion_id"],
                    "medico_id": p["medico_id"],
                    "razon": "importe_cero_o_negativo",
                })
                continue

            detalle_item = DetalleLiquidacion(
                liquidacion_id=liq.id,
                medico_id=p["medico_id"],
                obra_social_id=os_id,
                prestacion_id=p["prestacion_id"],
                pagado=Decimal("0"),
                importe=p["importe"],
            )
            db.add(detalle_item)
            existing.add(key)

    await db.flush()

    if observados:
        # Log observaciones (no bloquea, solo registra)
        import json
        print(f"[build_detalles] Observaciones en liq {liquidacion_id}: {json.dumps(observados)}")


# ================================================
# Recalcular totales de una liquidación (OS)
# ================================================
async def recalcular_totales_de_liquidacion(db: AsyncSession, liquidacion_id: int) -> None:
    liq = (await db.execute(
        select(Liquidacion).where(Liquidacion.id == liquidacion_id)
    )).scalars().first()
    if not liq:
        return

    # Bruto: suma de importes de detalles
    bruto_res = await db.execute(
        select(func.coalesce(func.sum(DetalleLiquidacion.importe), 0))
        .where(DetalleLiquidacion.liquidacion_id == liquidacion_id)
    )
    total_bruto = to_dec(bruto_res.scalar_one())

    # AGENT DO IT: DCs ahora vienen vía detalle_liquidacion_id (N DCs por detalle)
    dc_res = await db.execute(
        select(
            Debito_Credito.tipo,
            func.coalesce(func.sum(Debito_Credito.monto), 0),
        )
        .join(DetalleLiquidacion, Debito_Credito.detalle_liquidacion_id == DetalleLiquidacion.id)
        .where(DetalleLiquidacion.liquidacion_id == liquidacion_id)
        .group_by(Debito_Credito.tipo)
    )

    sum_debitos = Decimal("0")
    sum_creditos = Decimal("0")
    for tipo, total in dc_res.all():
        if tipo == "d":
            sum_debitos += to_dec(total)
        elif tipo == "c":
            sum_creditos += to_dec(total)

    liq.total_bruto = total_bruto
    liq.total_debitos = sum_debitos
    liq.total_neto = total_bruto - sum_debitos + sum_creditos

    await db.flush()


# ================================================
# Recalcular totales de un resumen global
# ================================================
async def recalcular_resumen_liquidacion(db: AsyncSession, resumen_id: int) -> Dict[str, Decimal]:
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

    # AGENT DO IT: usar resumen_id en DeduccionAplicacion (antes usaba created_at extraído)
    qd = await db.execute(
        select(func.coalesce(func.sum(DeduccionAplicacion.aplicado), 0))
        .where(DeduccionAplicacion.resumen_id == resumen_id)
    )
    total_deduccion = Decimal(str(qd.scalar_one() or 0)).quantize(Decimal("0.01"))

    total_neto = (total_bruto - (total_debitos + total_deduccion)).quantize(Decimal("0.01"))

    return {
        "total_bruto": total_bruto,
        "total_debitos": total_debitos,
        "total_deduccion": total_deduccion,
        "total_neto": total_neto,
    }


# ================================================
# Vista enriquecida de detalles de una liquidación
# ================================================
async def vista_detalles_liquidacion(
    db: AsyncSession,
    liquidacion_id: int,
    medico_id: Optional[int] = None,
    search: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    DL = DetalleLiquidacion
    GA = GuardarAtencion
    DC = Debito_Credito
    LM = aliased(ListadoMedico)

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
                        LM.NRO_SOCIO == n,
                        DL.medico_id == n,
                        GA.CODIGO_PRESTACION == s,
                    )
                )
            else:
                like = f"%{s}%"
                filters.append(
                    or_(
                        LM.NOMBRE.like(like),
                        GA.CODIGO_PRESTACION.like(like),
                    )
                )

    # AGENT DO IT: prestacion_id ahora es Integer FK → no necesita cast BigInteger
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
        .outerjoin(GA, DL.prestacion_id == GA.ID)  # FK directa, sin cast
        .outerjoin(LM, LM.NRO_SOCIO == DL.medico_id)
        .where(and_(*filters))
        .order_by(DL.id)
    )

    base_rows = (await db.execute(stmt_base)).mappings().all()
    if not base_rows:
        return [], 0

    # Obtener todos los DCs por detalle (N DCs por detalle)
    det_ids = [int(r["det_id"]) for r in base_rows]

    dc_map: dict[int, list[dict[str, Any]]] = {}
    if det_ids:
        stmt_dc = (
            select(
                DC.id.label("dc_id"),
                DC.detalle_liquidacion_id.label("det_id"),
                DC.tipo.label("tipo"),
                DC.monto.label("monto"),
                DC.observacion.label("obs"),
            )
            .where(DC.detalle_liquidacion_id.in_(det_ids))
            .order_by(DC.id)
        )

        dc_rows = (await db.execute(stmt_dc)).mappings().all()

        for d in dc_rows:
            det_id = int(d["det_id"])
            tipo = (d["tipo"] or "").lower()
            tipo_ui = "D" if tipo == "d" else "C" if tipo == "c" else None
            if not tipo_ui:
                continue

            dc_map.setdefault(det_id, []).append({
                "dc_id": int(d["dc_id"]),
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

        det_id = int(r["det_id"])
        dc_list = dc_map.get(det_id, [])

        sum_c = sum(Decimal(str(dc["monto"] or 0)) for dc in dc_list if dc["tipo"] == "C")
        sum_d = sum(Decimal(str(dc["monto"] or 0)) for dc in dc_list if dc["tipo"] == "D")

        total = importe + sum_c - sum_d

        out.append({
            "det_id": det_id,
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
# Generar / recalcular LiquidacionMedico para un resumen
# AGENT DO IT: tabla resumen por médico para trazabilidad y recibos
# ================================================
async def generar_liquidacion_medico(db: AsyncSession, resumen_id: int) -> List[Dict[str, Any]]:
    """
    Calcula y persiste/actualiza LiquidacionMedico para todos los médicos del resumen.
    Fórmula: reconocido = bruto + creditos - debitos
             neto_a_pagar = reconocido - deducciones
    Si neto < 0 → neto = 0 (el exceso queda en saldo).
    """
    resumen = await db.get(LiquidacionResumen, resumen_id)
    if not resumen:
        raise HTTPException(404, "Resumen no encontrado")

    # Bruto por médico
    # DetalleLiquidacion.medico_id guarda NRO_SOCIO; resolvemos al ID real de listado_medico
    # mediante JOIN para que la FK de LiquidacionMedico sea válida.
    bruto_q = await db.execute(
        select(
            ListadoMedico.ID.label("medico_db_id"),
            func.coalesce(func.sum(DetalleLiquidacion.importe), 0).label("bruto"),
        )
        .select_from(DetalleLiquidacion)
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .join(ListadoMedico, ListadoMedico.NRO_SOCIO == DetalleLiquidacion.medico_id)
        .where(Liquidacion.resumen_id == resumen_id)
        .group_by(ListadoMedico.ID)
    )
    bruto_map = {int(m): to_dec(b) for m, b in bruto_q.all()}

    # Débitos y créditos por médico (via detalle → DC), resolviendo NRO_SOCIO → ID
    dc_q = await db.execute(
        select(
            ListadoMedico.ID.label("medico_db_id"),
            Debito_Credito.tipo,
            func.coalesce(func.sum(Debito_Credito.monto), 0).label("total"),
        )
        .select_from(Debito_Credito)
        .join(DetalleLiquidacion, Debito_Credito.detalle_liquidacion_id == DetalleLiquidacion.id)
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .join(ListadoMedico, ListadoMedico.NRO_SOCIO == DetalleLiquidacion.medico_id)
        .where(Liquidacion.resumen_id == resumen_id)
        .group_by(ListadoMedico.ID, Debito_Credito.tipo)
    )
    deb_map: dict[int, Decimal] = {}
    cred_map: dict[int, Decimal] = {}
    for med_id, tipo, total in dc_q.all():
        med_id = int(med_id)
        if tipo == "d":
            deb_map[med_id] = deb_map.get(med_id, Decimal("0")) + to_dec(total)
        elif tipo == "c":
            cred_map[med_id] = cred_map.get(med_id, Decimal("0")) + to_dec(total)

    # Deducciones aplicadas por médico en este resumen
    ded_q = await db.execute(
        select(
            DeduccionAplicacion.medico_id,
            func.coalesce(func.sum(DeduccionAplicacion.aplicado), 0).label("total"),
        )
        .where(DeduccionAplicacion.resumen_id == resumen_id)
        .group_by(DeduccionAplicacion.medico_id)
    )
    ded_map = {int(m): to_dec(t) for m, t in ded_q.all()}

    todos_medicos = set(bruto_map) | set(deb_map) | set(cred_map) | set(ded_map)

    # Obtener NRO_SOCIO para mostrar en el front
    nro_socio_map: dict[int, int] = {}
    if todos_medicos:
        ns_rows = (await db.execute(
            select(ListadoMedico.ID, ListadoMedico.NRO_SOCIO)
            .where(ListadoMedico.ID.in_(list(todos_medicos)))
        )).all()
        nro_socio_map = {int(r.ID): int(r.NRO_SOCIO) for r in ns_rows}

    resultado = []

    for med_id in todos_medicos:
        bruto = bruto_map.get(med_id, Decimal("0"))
        debitos = deb_map.get(med_id, Decimal("0"))
        creditos = cred_map.get(med_id, Decimal("0"))
        deducciones = ded_map.get(med_id, Decimal("0"))

        reconocido = bruto + creditos - debitos
        neto_raw = reconocido - deducciones
        # Regla: neto negativo → neto = 0
        neto_a_pagar = max(neto_raw, Decimal("0"))

        # Upsert LiquidacionMedico
        existing = (await db.execute(
            select(LiquidacionMedico).where(
                LiquidacionMedico.resumen_id == resumen_id,
                LiquidacionMedico.medico_id == med_id,
            )
        )).scalars().first()

        if existing:
            existing.bruto = bruto
            existing.debitos = debitos
            existing.creditos = creditos
            existing.reconocido = reconocido
            existing.deducciones = deducciones
            existing.neto_a_pagar = neto_a_pagar
            existing.estado = "liquidado"
        else:
            lm = LiquidacionMedico(
                resumen_id=resumen_id,
                medico_id=med_id,
                bruto=bruto,
                debitos=debitos,
                creditos=creditos,
                reconocido=reconocido,
                deducciones=deducciones,
                neto_a_pagar=neto_a_pagar,
                estado="liquidado",
            )
            db.add(lm)

        resultado.append({
            "medico_id": med_id,
            "nro_socio": nro_socio_map.get(med_id),
            "bruto": float(bruto),
            "debitos": float(debitos),
            "creditos": float(creditos),
            "reconocido": float(reconocido),
            "deducciones": float(deducciones),
            "neto_a_pagar": float(neto_a_pagar),
        })

    await db.flush()
    return resultado


# ================================================
# Emitir recibos para un resumen
# AGENT DO IT: Recibo por médico, solo cuando liquidación está cerrada
# ================================================
async def emitir_recibos(db: AsyncSession, resumen_id: int) -> List[Dict[str, Any]]:
    """
    Genera (o regenera) recibos para todos los médicos del resumen.
    Precondición: al menos una liquidación del resumen debe estar cerrada.
    El nro_recibo se genera como '{resumen_id}-{medico_id}'.
    """
    resumen = await db.get(LiquidacionResumen, resumen_id)
    if not resumen:
        raise HTTPException(404, "Resumen no encontrado")

    # Verificar que exista al menos una liquidación cerrada
    closed = (await db.execute(
        select(Liquidacion.id).where(
            Liquidacion.resumen_id == resumen_id,
            Liquidacion.estado == "C",
        ).limit(1)
    )).first()
    if not closed:
        raise HTTPException(
            409,
            "No hay liquidaciones cerradas en este resumen. Solo se emiten recibos cuando la liquidación está cerrada.",
        )

    # Obtener LiquidacionMedico del resumen
    lm_rows = (await db.execute(
        select(LiquidacionMedico).where(LiquidacionMedico.resumen_id == resumen_id)
    )).scalars().all()

    if not lm_rows:
        raise HTTPException(
            409,
            "Ejecutar generar_liquidacion_medico antes de emitir recibos.",
        )

    # Obtener liquidaciones del resumen para los items del recibo
    liquidaciones = (await db.execute(
        select(Liquidacion).where(Liquidacion.resumen_id == resumen_id)
    )).scalars().all()

    now = datetime.datetime.now()
    resultado = []

    for lm in lm_rows:
        nro = f"{resumen_id:04d}-{lm.medico_id}"

        # Verificar si ya existe recibo
        existing = (await db.execute(
            select(Recibo).where(
                Recibo.resumen_id == resumen_id,
                Recibo.medico_id == lm.medico_id,
            )
        )).scalars().first()

        if existing:
            if existing.estado == "anulado":
                existing.estado = "emitido"
                existing.emision_timestamp = now
                existing.total_neto = lm.neto_a_pagar
            recibo = existing
        else:
            recibo = Recibo(
                nro_recibo=nro,
                resumen_id=resumen_id,
                medico_id=lm.medico_id,
                total_neto=lm.neto_a_pagar,
                emision_timestamp=now,
                estado="emitido",
            )
            db.add(recibo)
            await db.flush()

            # Items del recibo: uno por liquidación donde el médico tiene detalles
            for liq in liquidaciones:
                sub = (await db.execute(
                    select(func.coalesce(func.sum(DetalleLiquidacion.importe), 0))
                    .where(
                        DetalleLiquidacion.liquidacion_id == liq.id,
                        DetalleLiquidacion.medico_id == lm.medico_id,
                    )
                )).scalar_one()
                if sub and Decimal(str(sub)) > 0:
                    item = ReciboItem(
                        recibo_id=recibo.id,
                        liquidacion_id=liq.id,
                        concepto=f"OS {liq.obra_social_id} - {liq.anio_periodo}/{liq.mes_periodo:02d} - {liq.nro_factura or ''}",
                        importe=to_dec(sub),
                    )
                    db.add(item)

        resultado.append({
            "medico_id": lm.medico_id,
            "nro_recibo": recibo.nro_recibo if existing else nro,
            "total_neto": float(lm.neto_a_pagar),
            "estado": "emitido",
        })

    await db.flush()
    return resultado
