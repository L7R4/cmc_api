# app/services/liquidaciones.py
from __future__ import annotations
from typing import Dict, Any, List, Set, Tuple
from decimal import Decimal
import re, datetime
from sqlalchemy import select, or_, and_, exists
from sqlalchemy.ext.asyncio import AsyncSession

# modelos legados mapeados por sqlacodegen (ajusta nombres si difieren)
from app.db.models import GuardarAtencion, ObrasSociales, ListadoMedico, DetalleLiquidacion

_rx = re.compile(r"^\s*(\d{4})[-/](\d{1,2})\s*$")
def _normalizar_periodo(p: str) -> str | None:
    m = _rx.match(p)
    if not m: return None
    y, mth = int(m.group(1)), int(m.group(2))
    if y < 1900 or y > 3000 or not (1 <= mth <= 12): return None
    return f"{y:04d}-{mth:02d}"

def _separar_year_month(periodo: str) -> Tuple[int,int]:
    y, m = periodo.split("-")
    return int(y), int(m)

def _periodo_from_fecha(fecha) -> str | None:
    if not fecha: return None
    if isinstance(fecha, datetime.date):
        return f"{fecha.year:04d}-{fecha.month:02d}"
    if isinstance(fecha, str) and len(fecha) >= 7:
        return fecha[:7]
    return None

async def generar_preview(
    db: AsyncSession,
    obra_sociales_solicitadas: List[int],
    periodos_solicitados: List[str],
) -> Dict[str, Any]:

    # 1) validaciones sobre los inputs solicitados -> Obra social y Periodo =================

    if not obra_sociales_solicitadas:
        return {"status":"error","message":"obra_sociales_solicitadas vacío"}
    periodos_normalizados = [_normalizar_periodo(p) for p in periodos_solicitados]
    if any(p is None for p in periodos_normalizados):
        return {"status":"error","message":"periodos_solicitados inválidos; use YYYY-MM"}
    
    # =======================================================================================


    # 2) mapear OS: acepta código numérico ===================================================
    requested_codes = set(int(x) for x in obra_sociales_solicitadas if x is not None)

    # Traemos solo las OS pedidas (eficiente) y construimos el mapa código->nombre
    result = (await db.execute(
        select(ObrasSociales.NRO_OBRASOCIAL, ObrasSociales.OBRA_SOCIAL)
        .where(ObrasSociales.NRO_OBRASOCIAL.in_(requested_codes))
    )).all()

    # Transformar a dict {cod_os: name_os} para fácil lookup
    code2name = {cod_os: name_os for (cod_os, name_os) in result}
    if code2name == {}:
        return {"status":"error","message":"No se encontraron obras sociales válidas"}

    found_codes = set(code2name.keys())
    unknown = sorted(requested_codes - found_codes)
    if unknown:
        return {"status": "error", "message": f"Códigos de obra social inexistentes: {unknown}"}

    os_codes = found_codes
    # obra_sociales_nombres = [code2name[c] for c in sorted(os_codes)]
    # =======================================================================================
    
    # 3) query ORM a GuardarAtencion con:
    #    - filtro por OS
    #    - filtro por (ANIO_PERIODO, MES_PERIODO) ∈ periodos
    #    - excluir las YA LIQUIDADAS: NOT EXISTS detalle_liquidacion.prestacion_id == GA.ID
    yms = [_separar_year_month(p) for p in periodos_normalizados]
    print("\n yms:", yms) 

    per_conds = or_(*[and_(GuardarAtencion.ANIO_PERIODO == y, GuardarAtencion.MES_PERIODO == m) for y, m in yms])

    liq_exists = exists().where(DetalleLiquidacion.prestacion_id == GuardarAtencion.ID)

    stmt = (
        select(
            GuardarAtencion.ID.label("id_atencion"),
            GuardarAtencion.NRO_SOCIO.label("medico_id"),
            GuardarAtencion.NRO_OBRA_SOCIAL.label("os_id"),
            GuardarAtencion.CODIGO_PRESTACION.label("cod_prest"),
            GuardarAtencion.FECHA_PRESTACION.label("fecha"),
            GuardarAtencion.ANIO_PERIODO.label("anio_p"),
            GuardarAtencion.MES_PERIODO.label("mes_p"),
            GuardarAtencion.IMPORTE_COLEGIO.label("importe"),
            GuardarAtencion.GASTOS.label("gastos"),
            GuardarAtencion.CANTIDAD.label("cantidad"),
        )
        .where(
            GuardarAtencion.NRO_OBRA_SOCIAL.in_(os_codes),
            per_conds,
            ~liq_exists,  # excluye liquidadas
        )
    )

    rows = (await db.execute(stmt)).mappings().all()

    # nombre real del médico
    med_ids = {int(r["medico_id"]) for r in rows if r["medico_id"] is not None}
    medmap = {}
    if med_ids:
        res = await db.execute(
            select(ListadoMedico.NRO_SOCIO, ListadoMedico.NOMBRE)
            .where(ListadoMedico.NRO_SOCIO.in_(med_ids))
        )
        medmap = {r[0]: r[1] for r in res.all()}

    # 4) postproceso (duplicados, mismatches, montos) + agrupación
    omitidas: List[Dict[str,Any]] = []
    vistos: Set[int] = set()
    por_medico: Dict[int, Dict[str, Any]] = {}
    resumen = {"bruto":0.0,"descuentos":0.0,"retenciones":0.0,"ajustes":0.0,"neto":0.0}
    incluidas = 0

    for r in rows:
        rid = int(r["id_atencion"])
        if rid in vistos:
            omitidas.append({"id_atencion": f"GA-{rid}", "motivo":"DUPLICATED", "detalle":"repetida"})
            continue
        vistos.add(rid)

        per_anio_mes = f'{int(r["anio_p"]):04d}-{int(r["mes_p"]):02d}'
        per_fecha = _periodo_from_fecha(r["fecha"])
        if per_fecha and per_fecha != per_anio_mes and per_anio_mes not in periodos_normalizados:
            omitidas.append({"id_atencion": f"GA-{rid}", "motivo":"PERIODO_MISMATCH", "detalle": f"fecha={per_fecha} vs periodo={per_anio_mes}"})
            continue

        cantidad = int(r["cantidad"] or 1)
        bruto = float(Decimal(r["importe"] or 0) + Decimal(r["gastos"] or 0)) * cantidad
        descuentos = 0.0; retenciones = 0.0; ajuste = 0.0
        neto = bruto - descuentos - retenciones + ajuste

        mid = int(r["medico_id"])
        osid = int(r["os_id"])
        osname = code2name.get(osid, str(osid))

        m = por_medico.setdefault(mid, {"medico_id": mid, "medico_nombre": medmap.get(mid, f"Médico {mid}"), "obras_sociales": {}})
        osb = m["obras_sociales"].setdefault(osname, {})
        pb = osb.setdefault(per_anio_mes, {"periodo": per_anio_mes, "totales":{"bruto":0.0,"descuentos":0.0,"retenciones":0.0,"ajustes":0.0,"neto":0.0}, "prestaciones":[]})

        pb["prestaciones"].append({
            "id_atencion": f"GA-{rid}",
            "codigo_prestacion": r["cod_prest"],
            "fecha": per_fecha,
            "bruto": bruto,
            "descuentos": descuentos,
            "retenciones": retenciones,
            "ajuste": ajuste,
            "neto": neto,
        })

        t = pb["totales"]
        t["bruto"] += bruto; t["descuentos"] += descuentos; t["retenciones"] += retenciones; t["ajustes"] += ajuste; t["neto"] += neto
        resumen["bruto"] += bruto; resumen["descuentos"] += descuentos; resumen["retenciones"] += retenciones; resumen["ajustes"] += ajuste; resumen["neto"] += neto
        incluidas += 1

    por_medico_out = []
    for mid, m in por_medico.items():
        os_out = [{"obra_social": osn, "periodos": list(pmap.values())} for osn, pmap in m["obras_sociales"].items()]
        por_medico_out.append({"medico_id": m["medico_id"], "medico_nombre": m["medico_nombre"], "obras_sociales": os_out})

    return {
      "status": "ok",
      "solicitud": {
        "obra_sociales": sorted({code2name.get(c, str(c)) for c in os_codes}),
        "periodos_normalizados": periodos_normalizados
      },
      "resumen": {
        "total_prestaciones_incluidas": incluidas,
        "total_omitidas": len(omitidas),
        "total_bruto": round(resumen["bruto"],2),
        "total_descuentos": round(resumen["descuentos"],2),
        "total_retenciones": round(resumen["retenciones"],2),
        "total_ajustes": round(resumen["ajustes"],2),
        "total_neto": round(resumen["neto"],2),
      },
      "por_medico": por_medico_out,
      "omitidas": omitidas
    }
