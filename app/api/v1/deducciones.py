from fastapi import APIRouter, Depends, HTTPException, Body, status
from pydantic import BaseModel
from typing import Optional, Literal, List, Dict
from decimal import Decimal
from sqlalchemy import select, text, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import (
    Debito_Credito, LiquidacionResumen, Descuentos, DeduccionColegio,
    DeduccionSaldo, DeduccionAplicacion,
    DetalleLiquidacion, Liquidacion
)

router = APIRouter()

class OverrideValores(BaseModel):
    monto: Optional[Decimal] = None
    porcentaje: Optional[Decimal] = None

def _tipo_id_for_desc(desc_id: int) -> tuple[str,int]:
    return ("desc", int(desc_id))

async def _base_bruto_por_medico_en_resumen(db: AsyncSession, resumen_id: int) -> dict[int, Decimal]:
    q = await db.execute(
        select(DetalleLiquidacion.medico_id, func.coalesce(func.sum(DetalleLiquidacion.importe), 0))
        .select_from(DetalleLiquidacion)
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .where(Liquidacion.resumen_id == resumen_id)
        .group_by(DetalleLiquidacion.medico_id)
    )
    out = {}
    for med_id, suma in q:
        out[int(med_id)] = Decimal(suma or 0)
    return out

async def _medicos_asociados_a_nro_concepto(db: AsyncSession, nro_concepto: int) -> List[int]:
    sql = text("""
      SELECT m.ID
      FROM listado_medico m
      WHERE (
        JSON_CONTAINS(JSON_EXTRACT(m.conceps_espec, '$.conceps'), CAST(:n AS JSON))
        OR JSON_CONTAINS(JSON_EXTRACT(m.conceps_espec, '$.conceps'), JSON_QUOTE(CAST(:n AS CHAR)))
      )
    """)
    rows = (await db.execute(sql, {"n": int(nro_concepto)})).all()
    return [int(r[0]) for r in rows]

@router.post("/{resumen_id}/colegio/bulk_generar_descuento/{desc_id}",
             status_code=status.HTTP_201_CREATED)
async def bulk_generar_descuento(
    resumen_id: int,
    desc_id: int,
    payload: Optional[OverrideValores] = Body(None),
    db: AsyncSession = Depends(get_db),
):
    # Validaciones básicas
    async with db.begin():
      res = await db.get(LiquidacionResumen, resumen_id)
      if not res:
          raise HTTPException(404, "Resumen no encontrado")

      desc = await db.get(Descuentos, desc_id)
      if not desc:
          raise HTTPException(404, "Descuento no encontrado")

      # snapshot (con override opcional del front)
      monto_snap = Decimal(str(payload.monto if payload and payload.monto is not None else desc.precio or 0))
      pct_snap   = Decimal(str(payload.porcentaje if payload and payload.porcentaje is not None else desc.porcentaje or 0))

      # Médicos asociados por nro_colegio (sin exigir actividad)
      med_ids = await _medicos_asociados_a_nro_concepto(db, int(desc.nro_colegio))

      # Base bruta del mes (para %); si no hay actividad, base=0
      base_por_med = await _base_bruto_por_medico_en_resumen(db, resumen_id)

      tipo, concepto_id = _tipo_id_for_desc(desc_id)

      creados, actualizados = 0, 0
      total_cargado = Decimal("0")

      for med_id in med_ids:
          base = base_por_med.get(med_id, Decimal("0"))
          calculado = (monto_snap + (base * pct_snap / Decimal("100"))).quantize(Decimal("0.01"))
          # Upsert snapshot del mes (DeduccionColegio)
          row = (await db.execute(
              select(DeduccionColegio).where(
                  DeduccionColegio.resumen_id == resumen_id,
                  DeduccionColegio.medico_id == med_id,
                  DeduccionColegio.descuento_id == desc_id,
                  DeduccionColegio.especialidad_id.is_(None),
              )
          )).scalars().first()
          if row:
              row.monto_aplicado = monto_snap
              row.porcentaje_aplicado = pct_snap
              row.calculado_total = calculado
              actualizados += 1
          else:
              db.add(DeduccionColegio(
                  medico_id=med_id,
                  resumen_id=resumen_id,
                  descuento_id=desc_id,
                  especialidad_id=None,
                  monto_aplicado=monto_snap,
                  porcentaje_aplicado=pct_snap,
                  calculado_total=calculado,
              ))
              creados += 1
          total_cargado += calculado
          
          # Upsert saldo (sumar calculado)
          saldo = (await db.execute(
              select(DeduccionSaldo).where(
                  DeduccionSaldo.medico_id == med_id,
                  DeduccionSaldo.concepto_tipo == tipo,
                  DeduccionSaldo.concepto_id == concepto_id,
              ).with_for_update()
          )).scalars().first()
          if saldo:
              saldo.saldo = (Decimal(str(saldo.saldo or 0)) + calculado).quantize(Decimal("0.01"))
          else:
              db.add(DeduccionSaldo(
                  medico_id=med_id,
                  concepto_tipo=tipo,
                  concepto_id=concepto_id,
                  saldo=calculado,
              ))

      # No tocamos aún total_deduccion del resumen; eso lo hace /aplicar
      return {
          "resumen_id": resumen_id,
          "tipo": "descuento",
          "id_aplicado": desc_id,
          "generados": creados,
          "actualizados": actualizados,
          "cargado_total": float(total_cargado),
          "nota": "Se cargó el mes y se actualizó el saldo. Ejecutá /colegio/aplicar para descontar según disponible."
      }

async def _disponible_por_medico_en_resumen(db: AsyncSession, resumen_id: int) -> dict[int, Decimal]:
    # bruto por médico
    bruto = await db.execute(
        select(DetalleLiquidacion.medico_id, func.coalesce(func.sum(DetalleLiquidacion.importe), 0))
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .where(Liquidacion.resumen_id == resumen_id)
        .group_by(DetalleLiquidacion.medico_id)
    )
    bruto_map = {int(m): Decimal(v or 0) for m, v in bruto}

    # débitos y créditos por médico (por DC ligados a sus detalles del resumen)
    qdc = await db.execute(
        select(
            DetalleLiquidacion.medico_id,
            func.coalesce(func.sum(case((Debito_Credito.tipo=="d", Debito_Credito.monto), else_=0)), 0),
            func.coalesce(func.sum(case((Debito_Credito.tipo=="c", Debito_Credito.monto), else_=0)), 0),
        )
        .select_from(DetalleLiquidacion)
        .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
        .join(Debito_Credito, DetalleLiquidacion.debito_credito_id == Debito_Credito.id, isouter=True)
        .where(Liquidacion.resumen_id == resumen_id)
        .group_by(DetalleLiquidacion.medico_id)
    )
    deb_map, cred_map = {}, {}
    for med, deb, cred in qdc:
        deb_map[int(med)] = Decimal(deb or 0)
        cred_map[int(med)] = Decimal(cred or 0)

    # disponible = bruto - déb + créd
    out: dict[int, Decimal] = {}
    keys = set(bruto_map) | set(deb_map) | set(cred_map)
    for k in keys:
        out[k] = (bruto_map.get(k, Decimal("0")) - deb_map.get(k, Decimal("0")) + cred_map.get(k, Decimal("0")))
    return out

@router.post("/{resumen_id}/colegio/aplicar")
async def aplicar_deducciones_resumen(resumen_id: int, db: AsyncSession = Depends(get_db)):
    async with db.begin():
        res = await db.get(LiquidacionResumen, resumen_id)
        if not res:
            raise HTTPException(404, "Resumen no encontrado")

        disponible = await _disponible_por_medico_en_resumen(db, resumen_id)  # {med: Decimal}

        aplicados_total = Decimal("0")
        medicos_afectados = set()

        # Traer TODOS los saldos > 0 para los médicos que tengan algo en este momento
        rows = (await db.execute(
            select(DeduccionSaldo).where(DeduccionSaldo.saldo > 0)
        )).scalars().all()

        # Agrupar por médico y ordenar por saldo desc (maximiza descuento)
        saldos_by_med: dict[int, List[DeduccionSaldo]] = {}
        for s in rows:
            saldos_by_med.setdefault(int(s.medico_id), []).append(s)
        for med in saldos_by_med:
            saldos_by_med[med].sort(key=lambda r: Decimal(str(r.saldo or 0)), reverse=True)

        for med_id, sal_list in saldos_by_med.items():
            disp = Decimal(str(disponible.get(med_id, Decimal("0")) or 0))
            if disp <= 0:
                continue

            for s in sal_list:
                if disp <= 0:
                    break
                saldo_actual = Decimal(str(s.saldo or 0))
                if saldo_actual <= 0:
                    continue

                aplicar = min(disp, saldo_actual)
                if aplicar <= 0:
                    continue

                # ↓ saldo
                s.saldo = (saldo_actual - aplicar).quantize(Decimal("0.01"))

                # Upsert aplicación del mes
                apl = (await db.execute(
                    select(DeduccionAplicacion).where(
                        DeduccionAplicacion.resumen_id == resumen_id,
                        DeduccionAplicacion.medico_id == med_id,
                        DeduccionAplicacion.concepto_tipo == s.concepto_tipo,
                        DeduccionAplicacion.concepto_id == s.concepto_id,
                    ).with_for_update()
                )).scalars().first()

                if apl:
                    apl.aplicado = (Decimal(str(apl.aplicado or 0)) + aplicar).quantize(Decimal("0.01"))
                else:
                    db.add(DeduccionAplicacion(
                        resumen_id=resumen_id,
                        medico_id=med_id,
                        concepto_tipo=s.concepto_tipo,
                        concepto_id=s.concepto_id,
                        aplicado=aplicar,
                    ))

                disp -= aplicar
                aplicados_total += aplicar
                medicos_afectados.add(med_id)

        # Recalcular total_deduccion del resumen = Σ aplicado del mes
        qsum = await db.execute(
            select(func.coalesce(func.sum(DeduccionAplicacion.aplicado), 0))
            .where(DeduccionAplicacion.resumen_id == resumen_id)
        )
        res.total_deduccion = Decimal(qsum.scalar_one() or 0).quantize(Decimal("0.01"))

        # Podés también refrescar totales generales del resumen si querés
        # await recomputar_totales_de_resumen(db, resumen_id)

    return {
        "resumen_id": resumen_id,
        "medicos_afectados": len(medicos_afectados),
        "aplicado_total": float(aplicados_total),
        "nota": "Se aplicó lo máximo posible por médico. El remanente queda en saldos para próximos meses."
    }