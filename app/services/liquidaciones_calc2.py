# services/liquidaciones_calc.py
from __future__ import annotations
from typing import Any, Dict, List, Mapping, Optional, Tuple
from decimal import Decimal

from sqlalchemy import and_, exists, or_, select, func, String, cast
from sqlalchemy.ext.asyncio import AsyncSession

# Ajusta los imports al layout real de tu proyecto
from app.db.models import (
    GuardarAtencion,           # tabla fuente
    Liquidacion,               # tu "LiquidacionPorOS"
    LiquidacionResumen,
    DetalleLiquidacion,
    Debito_Credito,
    ObrasSociales,
)
def to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")
    
def _to_int(x) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(x)
    except Exception:
        return None
    

def period_str(anio: int, mes: int) -> str:
    return f"{int(anio):04d}-{int(mes):02d}"

def to_dec(x) -> Decimal:
    try:
        return Decimal(str(x or "0")).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0")
    
# -------------------------------------------------------------------
# 1) Descomponer UNA fila de GuardarAtencion a actores pagables
# -------------------------------------------------------------------
def descomponer_row_a_actores(
    row: Mapping[str, Any],
    *,
    multiplicar_ayudantes_por_factor: bool = False,
) -> List[Dict[str, Any]]:
    """
    Devuelve actores pagables para una fila de GuardarAtencion:
    - médico principal: valor_cirugia * (cantidad * cant_tratamiento)
    - ayudante(s): valor_ayudante(_2) * factor (opcional)
    """
    id_atencion = _to_int(row.get("id_atencion"))
    if id_atencion is None:
        return []

    anio = _to_int(row.get("anio_periodo")) or 0
    mes = _to_int(row.get("mes_periodo")) or 0
    periodo = f"{anio:04d}-{mes:02d}"

    cantidad = _to_int(row.get("cantidad")) or 1
    cant_trat = _to_int(row.get("cantidad_tratamiento")) or 1
    factor = cantidad * cant_trat

    out: List[Dict[str, Any]] = []

    # Médico principal
    med_id = _to_int(row.get("medico_id"))
    if med_id is not None:
        bruto = to_decimal(row.get("valor_cirugia")) * Decimal(factor)
        out.append(dict(
            actor_id=med_id,
            bruto=bruto,
            id_atencion=id_atencion,
            periodo=periodo,
        ))

    # Ayudante 1
    ay1_id = _to_int(row.get("nro_socio_ayudante"))
    if ay1_id is not None:
        base = to_decimal(row.get("valor_ayudante"))
        bruto = base * (Decimal(factor) if multiplicar_ayudantes_por_factor else Decimal(1))
        out.append(dict(
            actor_id=ay1_id,
            bruto=bruto,
            id_atencion=id_atencion,
            periodo=periodo,
        ))

    # Ayudante 2
    ay2_id = _to_int(row.get("nro_socio_ayudante_2"))
    if ay2_id is not None:
        base = to_decimal(row.get("valor_ayudante_2"))
        bruto = base * (Decimal(factor) if multiplicar_ayudantes_por_factor else Decimal(1))
        out.append(dict(
            actor_id=ay2_id,
            bruto=bruto,
            id_atencion=id_atencion,
            periodo=periodo,
        ))

    return out


# -------------------------------------------------------------------
# 2) Calcular totales para UNA OS + UN período (YYYY-MM)
#    Excluye prestaciones ya liquidadas (DetalleLiquidacion)
# -------------------------------------------------------------------
async def calcular_bruto_y_actores(
    db: AsyncSession,
    *,
    obra_social_id: int,
    anio: int,
    mes: int,
    excluir_ya_liquidadas: bool = True,
    multiplicar_ayudantes_por_factor: bool = False,
) -> Dict[str, Any]:
    """
    Suma el BRUTO por actores (médico/ayudantes) para una OS+período.
    Excluye prestaciones ya liquidadas según DetalleLiquidacion.prestacion_id.
    """
    where = [
        GuardarAtencion.NRO_OBRA_SOCIAL == obra_social_id,
        GuardarAtencion.ANIO_PERIODO == anio,
        GuardarAtencion.MES_PERIODO == mes,
    ]

    if excluir_ya_liquidadas:
        # DetalleLiquidacion.prestacion_id es String; la fuente es INT ⇒ CAST
        existe = exists().where(
            DetalleLiquidacion.prestacion_id == cast(GuardarAtencion.ID, String)
        )
        where.append(~existe)

    q = select(
        GuardarAtencion.ID.label("id_atencion"),
        GuardarAtencion.NRO_SOCIO.label("medico_id"),
        GuardarAtencion.NRO_OBRA_SOCIAL.label("obra_social_id"),
        GuardarAtencion.ANIO_PERIODO.label("anio_periodo"),
        GuardarAtencion.MES_PERIODO.label("mes_periodo"),
        GuardarAtencion.VALOR_CIRUJIA.label("valor_cirugia"),
        GuardarAtencion.VALOR_AYUDANTE.label("valor_ayudante"),
        GuardarAtencion.VALOR_AYUDANTE_2.label("valor_ayudante_2"),
        GuardarAtencion.CANTIDAD.label("cantidad"),
        GuardarAtencion.CANT_TRATAMIENTO.label("cantidad_tratamiento"),
        GuardarAtencion.NRO_CONSULTA.label("nro_consulta"),
        GuardarAtencion.EXISTE.label("existe"),
    ).where(*where)

    rows = (await db.execute(q)).mappings().all()

    vistos: set[Tuple[int, int]] = set()  # (id_atencion, actor_id)
    actores: List[Dict[str, Any]] = []
    total_bruto = Decimal("0.00")

    for r in rows:
        # mismos filtros que usás en tu servicio de preview
        if r.get("existe") != "S":
            continue
        if r.get("nro_consulta") in ["0", "1"] or r.get("nro_consulta") is None:
            continue

        partes = descomponer_row_a_actores(
            r, multiplicar_ayudantes_por_factor=multiplicar_ayudantes_por_factor
        )
        for a in partes:
            clave = (int(a["id_atencion"]), int(a["actor_id"]))
            if clave in vistos:
                continue
            vistos.add(clave)
            actores.append(a)
            total_bruto += to_decimal(a["bruto"])

    return {"total_bruto": total_bruto, "actores": actores}



# -------------------------------------------------------------------
# 3) Débitos/Créditos con filtros
#     - Debito_Credito.tipo: 'd' (débito) | 'c' (crédito)
#     - Para filtrar por médico unimos a GuardarAtencion vía id_atencion
# -------------------------------------------------------------------
async def obtener_debitos_creditos(
    db: AsyncSession,
    *,
    obra_social_id: Optional[int] = None,
    periodo: Optional[str] = None,      # "YYYY-MM"
    medico_id: Optional[int] = None,    # NRO_SOCIO (vía join)
) -> List[Dict[str, Any]]:
    """
    Lista débitos/créditos. Si se pasa medico_id se une a GuardarAtencion.
    """
    from sqlalchemy import join

    base = select(
        Debito_Credito.id,
        Debito_Credito.tipo,
        Debito_Credito.id_atencion,
        Debito_Credito.obra_social_id,
        Debito_Credito.observacion,
        Debito_Credito.monto,
        Debito_Credito.periodo,
    )
    if medico_id is not None:
        j = join(Debito_Credito, GuardarAtencion, Debito_Credito.id_atencion == GuardarAtencion.ID)
        base = base.select_from(j).add_columns(
            GuardarAtencion.NRO_SOCIO.label("medico_id")
        )

    cond = []
    if obra_social_id is not None:
        cond.append(Debito_Credito.obra_social_id == obra_social_id)
    if periodo:
        cond.append(Debito_Credito.periodo == periodo)
    if medico_id is not None:
        cond.append(GuardarAtencion.NRO_SOCIO == medico_id)

    if cond:
        base = base.where(and_(*cond))

    rows = (await db.execute(base)).mappings().all()
    return [dict(r) for r in rows]


# -------------------------------------------------------------------
# 4) Crear Liquidacion (OS+Periodo) calculando totales y generando DetalleLiquidacion
# -------------------------------------------------------------------
async def crear_liquidacion_con_totales(
    db: AsyncSession,
    *,
    resumen_id: int,
    obra_social_id: int,
    anio_periodo: int,
    mes_periodo: int,
    nro_liquidacion: str,
    multiplicar_ayudantes_por_factor: bool = False,
) -> Liquidacion:
    """
    Crea la Liquidacion (única por resumen/OS/año/mes),
    calcula BRUTO a partir de GuardarAtencion (actorizando),
    computa débitos/créditos del período y genera DetalleLiquidacion.
    """
    # Evitar duplicado por constraint lógico
    existe = await db.execute(
        select(Liquidacion.id).where(
            Liquidacion.resumen_id == resumen_id,
            Liquidacion.obra_social_id == obra_social_id,
            Liquidacion.anio_periodo == anio_periodo,
            Liquidacion.mes_periodo == mes_periodo,
        )
    )
    if existe.scalar_one_or_none():
        raise ValueError("Ya existe una liquidación para ese resumen/OS/período.")

    # BRUTO + actores
    calc = await calcular_bruto_y_actores(
        db,
        obra_social_id=obra_social_id,
        anio=anio_periodo,
        mes=mes_periodo,
        excluir_ya_liquidadas=True,
        multiplicar_ayudantes_por_factor=multiplicar_ayudantes_por_factor,
    )
    total_bruto: Decimal = calc["total_bruto"]
    actores: List[Dict[str, Any]] = calc["actores"]

    # D/C del período para la OS
    periodo = f"{anio_periodo:04d}-{mes_periodo:02d}"
    dcs = await obtener_debitos_creditos(
        db, obra_social_id=obra_social_id, periodo=periodo
    )
    total_debitos = Decimal("0.00")
    total_creditos = Decimal("0.00")
    for d in dcs:
        monto = to_decimal(d.get("monto"))
        if (d.get("tipo") or "d") == "d":
            total_debitos += monto
        else:
            total_creditos += monto

    debitos_neto = total_debitos - total_creditos
    total_neto = total_bruto - debitos_neto

    # Insert Liquidacion
    liq = Liquidacion(
        resumen_id=resumen_id,
        obra_social_id=obra_social_id,
        mes_periodo=mes_periodo,
        anio_periodo=anio_periodo,
        nro_liquidacion=nro_liquidacion,
        total_bruto=total_bruto,
        total_debitos=debitos_neto,
        total_neto=total_neto,
    )
    db.add(liq)
    await db.flush()  # obtener liq.id

    # Detalles (bloquean reliquidación futura al ser únicos por prestacion_id)
    detalles: List[DetalleLiquidacion] = []
    for a in actores:
        detalles.append(
            DetalleLiquidacion(
                liquidacion_id=liq.id,
                medico_id=int(a["actor_id"]),
                obra_social_id=obra_social_id,
                prestacion_id=str(int(a["id_atencion"])),  # UNIQUE
                debito_credito_id=None,
                bruto=to_decimal(a["bruto"]),
                debito_monto=Decimal("0.00"),
                deduccion_monto=Decimal("0.00"),
                neto=to_decimal(a["bruto"]),
            )
        )
    if detalles:
        db.add_all(detalles)

    return liq
