from collections import defaultdict
from decimal import Decimal
from operator import and_
from typing import DefaultDict, Optional, Dict, List
from fastapi import APIRouter, Body, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import desc, func, select, or_, cast, String
from app.db.database import get_db
from app.db.models import (
    DeduccionColegio, Descuentos, DetalleLiquidacion, Especialidad, Liquidacion, ListadoMedico,
    DeduccionSaldo, DeduccionAplicacion, LiquidacionResumen
)
from app.schemas.deduccion_schema import CrearDeudaOut, NuevaDeudaIn
from app.schemas.medicos_schema import (
    AsociarConceptoIn, CEAppOut, CEBundleOut, CEBundlePatchIn, CEStoreOut, ConceptRecordOut, ConceptoAplicacionOut, DoctorStatsPointOut, MedicoConceptoOut, MedicoDebtOut, MedicoDocOut, MedicoEspecialidadOut, MedicoListRow, MedicoDetailOut, PatchCEIn
)

router = APIRouter()

@router.get("", response_model=List[MedicoListRow])
async def listar_medicos(
    db: AsyncSession = Depends(get_db),
    q: Optional[str] = Query(None, description="Buscar por nombre, nro socio o matrículas"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    stmt = (
        select(
            ListadoMedico.ID.label("id"),
            ListadoMedico.NRO_SOCIO.label("nro_socio"),
            ListadoMedico.NOMBRE.label("nombre"),
            ListadoMedico.MATRICULA_PROV.label("matricula_prov"),
            ListadoMedico.DOCUMENTO.label("documento"),
        )
        .where(ListadoMedico.EXISTE == "S")
        .order_by(ListadoMedico.NOMBRE.asc())
        .offset(skip).limit(limit)
    )

    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                ListadoMedico.NOMBRE.ilike(pattern),
                cast(ListadoMedico.NRO_SOCIO, String).ilike(pattern),
                cast(ListadoMedico.MATRICULA_PROV, String).ilike(pattern),
                cast(ListadoMedico.DOCUMENTO, String).ilike(pattern),
            )
        )

    rows = (await db.execute(stmt)).mappings().all()
    return [dict(r) for r in rows]


@router.get("/{medico_id}", response_model=MedicoDetailOut)
async def obtener_medico(
    medico_id: int,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(
        ListadoMedico.ID.label("id"),
        ListadoMedico.NRO_SOCIO.label("nro_socio"),
        ListadoMedico.NOMBRE.label("nombre"),
        ListadoMedico.MATRICULA_PROV.label("matricula_prov"),
        ListadoMedico.MATRICULA_NAC.label("matricula_nac"),
        ListadoMedico.TELEFONO_CONSULTA.label("telefono_consulta"),
        ListadoMedico.DOMICILIO_CONSULTA.label("domicilio_consulta"),
        ListadoMedico.MAIL_PARTICULAR.label("mail_particular"),
        ListadoMedico.SEXO.label("sexo"),
        ListadoMedico.TIPO_DOC.label("tipo_doc"),
        ListadoMedico.DOCUMENTO.label("documento"),
        ListadoMedico.CUIT.label("cuit"),
        ListadoMedico.PROVINCIA.label("provincia"),
        ListadoMedico.CODIGO_POSTAL.label("codigo_postal"),
        ListadoMedico.CATEGORIA.label("categoria"),
        ListadoMedico.EXISTE.label("existe"),
        ListadoMedico.FECHA_NAC.label("fecha_nac"),
    ).where(ListadoMedico.ID == medico_id)

    row = (await db.execute(stmt)).mappings().first()
    if not row:
        raise HTTPException(404, "Médico no encontrado")
    return dict(row)


@router.get("/{medico_id}/deuda", response_model=MedicoDebtOut)
async def deuda_medico(medico_id: int, db: AsyncSession = Depends(get_db)):
    # total de saldo pendiente (todas las fuentes: descuentos, especialidades, manual, etc.)
    q_total = await db.execute(
        select(func.coalesce(func.sum(DeduccionSaldo.saldo), 0)).where(DeduccionSaldo.medico_id == medico_id)
    )
    total = Decimal(q_total.scalar_one() or 0)

    # último período en el que se aplicó deducción (si existe)
    q_last = await db.execute(
        select(LiquidacionResumen.anio, LiquidacionResumen.mes)
        .select_from(DeduccionAplicacion)
        .join(LiquidacionResumen, LiquidacionResumen.id == DeduccionAplicacion.resumen_id)
        .where(DeduccionAplicacion.medico_id == medico_id)
        .order_by(desc(LiquidacionResumen.anio), desc(LiquidacionResumen.mes))
        .limit(1)
    )
    row = q_last.first()
    last_invoice: Optional[str] = f"{row[0]:04d}-{int(row[1]):02d}" if row else None

    return {
        "has_debt": total > 0,
        "amount": total,
        "last_invoice": last_invoice,
        "since": None,  # si luego guardás timestamps de alta de saldo podés completarlo
    }


@router.post("/{medico_id}/deudas_manual", response_model=MedicoDebtOut, status_code=status.HTTP_201_CREATED)
async def crear_deuda_manual(
    medico_id: int,
    payload: NuevaDeudaIn = Body(...),
    db: AsyncSession = Depends(get_db),
):
    total = payload.amount if payload.mode == "full" else sum(Decimal(str(q.amount)) for q in (payload.installments or []))

    async with db.begin():  # una sola TX
        saldo = (await db.execute(
            select(DeduccionSaldo)
            .where(
                DeduccionSaldo.medico_id == medico_id,
                DeduccionSaldo.concepto_tipo == "manual",
                DeduccionSaldo.concepto_id == 0,
            )
            .with_for_update()
        )).scalars().first()

        if saldo:
            saldo.saldo = (Decimal(str(saldo.saldo or 0)) + total).quantize(Decimal("0.01"))
        else:
            db.add(DeduccionSaldo(
                medico_id=medico_id,
                concepto_tipo="manual",
                concepto_id=0,
                saldo=total.quantize(Decimal("0.01")),
            ))

        # devolver estado actualizado
        q_total = await db.execute(
            select(func.coalesce(func.sum(DeduccionSaldo.saldo), 0)).where(DeduccionSaldo.medico_id == medico_id)
        )
        total_out = Decimal(q_total.scalar_one() or 0).quantize(Decimal("0.01"))

        return {
            "has_debt": total_out > 0,
            "amount": total_out,
            "last_invoice": None,
            "since": None,
        }


@router.get("/{medico_id}/documentos", response_model=List[MedicoDocOut])
async def documentos_medico(
    medico_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Si tenés una tabla de documentos del médico, hacé SELECT aquí.
    De momento, devolvemos vacío.
    """
    return []


@router.get("/{medico_id}/stats", response_model=List[DoctorStatsPointOut])
async def stats_medico(
    medico_id: int,
    months: int = Query(6, ge=1, le=24),
    db: AsyncSession = Depends(get_db),
):
    """
    Estadísticas por mes, agrupando por liquidación:
      - consultas: COUNT(detalles)
      - facturado: SUM(DL.importe)
      - obras: desglose por obra_social_id (clave dinámica "OS {id}")
    Se arma con Liquidacion.anio_periodo / mes_periodo.
    """
    DL, LQ = DetalleLiquidacion, Liquidacion

    # Traemos: período + obra + (consultas, suma)
    rows = (await db.execute(
        select(
            LQ.anio_periodo.label("anio"),
            LQ.mes_periodo.label("mes"),
            DL.obra_social_id.label("os_id"),
            func.count(DL.id).label("consultas"),
            func.coalesce(func.sum(DL.importe), 0).label("facturado"),
        )
        .join(LQ, LQ.id == DL.liquidacion_id)
        .where(DL.medico_id == medico_id)
        .group_by(LQ.anio_periodo, LQ.mes_periodo, DL.obra_social_id)
        .order_by(LQ.anio_periodo.asc(), LQ.mes_periodo.asc())
    )).mappings().all()

    # Agregamos por (anio-mes)
    bucket: Dict[str, Dict] = {}
    for r in rows:
        y, m = int(r["anio"]), int(r["mes"])
        key = f"{y:04d}-{m:02d}"
        if key not in bucket:
            bucket[key] = {
                "consultas": 0,
                "facturado": Decimal("0"),
                "obras": defaultdict(Decimal),
            }
        bucket[key]["consultas"] += int(r["consultas"] or 0)
        bucket[key]["facturado"] += Decimal(str(r["facturado"] or 0))
        os_key = f"OS {int(r['os_id'])}"
        bucket[key]["obras"][os_key] += Decimal(str(r["facturado"] or 0))

    # Nos quedamos con los últimos N meses
    all_keys_sorted = sorted(bucket.keys())
    take_keys = all_keys_sorted[-months:]

    out: List[DoctorStatsPointOut] = []
    for k in take_keys:
        data = bucket[k]
        obras_map = {kk: float(v) for kk, v in data["obras"].items()}
        out.append(DoctorStatsPointOut(
            month=k,
            consultas=int(data["consultas"]),
            facturado=float(data["facturado"]),
            obras=obras_map
        ))
    return out


@router.patch("/{medico_id}/ce_bundle")
async def patch_ce_bundle(
    medico_id: int,
    payload: CEBundlePatchIn = Body(...),
    db: AsyncSession = Depends(get_db),
):
    # Validar existencia en catálogo
    if payload.concepto_tipo == "desc":
        exists = (await db.execute(
            select(Descuentos.nro_colegio)
            .where(Descuentos.nro_colegio == payload.concepto_id)
            .limit(1)
        )).scalar()
    else:  # "esp"
        exists = (await db.execute(
            select(Especialidad.ID).where(Especialidad.ID == payload.concepto_id).limit(1)
        )).scalar()

    if not exists:
        raise HTTPException(404, "Concepto/Especialidad no encontrado")

    # Lock del médico
    med = (await db.execute(
        select(ListadoMedico).where(ListadoMedico.ID == medico_id).with_for_update()
    )).scalars().first()
    if not med:
        raise HTTPException(404, "Médico no encontrado")

    cfg = dict(med.conceps_espec or {"conceps": [], "espec": []})
    cfg.setdefault("conceps", [])
    cfg.setdefault("espec", [])

    # Para desc guardamos nro_colegio; para esp el ID de Especialidad
    target = "conceps" if payload.concepto_tipo == "desc" else "espec"
    current = [int(x) for x in (cfg[target] or [])]

    if payload.op == "add":
        current.append(int(payload.concepto_id))
    else:
        current = [x for x in current if int(x) != int(payload.concepto_id)]

    cfg[target] = sorted(set(current))  # dedupe + orden
    med.conceps_espec = cfg
    await db.commit()
    return {"store": cfg}


@router.get("/{medico_id}/conceptos", response_model=List[MedicoConceptoOut])
async def listar_conceptos_medico(
    medico_id: int,
    db: AsyncSession = Depends(get_db),
):
    # 1) Leer store del médico (nro_colegio)
    store = (await db.execute(
        select(ListadoMedico.conceps_espec).where(ListadoMedico.ID == medico_id)
    )).scalar_one_or_none() or {"conceps": [], "espec": []}

    nro_list = [int(x) for x in (store.get("conceps") or [])]
    if not nro_list:
        return []

    # 2) Catálogo: map nro_colegio -> [ids] y un nombre de referencia
    rows = (await db.execute(
        select(Descuentos.id, Descuentos.nro_colegio, Descuentos.nombre)
        .where(Descuentos.nro_colegio.in_(nro_list))
    )).all()

    ids_by_nro: DefaultDict[int, List[int]] = defaultdict(list)
    name_by_nro: Dict[int, str] = {}
    for did, nro, nom in rows:
        n = int(nro)
        ids_by_nro[n].append(int(did))
        # elegimos el último nombre visto (o podrías elegir el de mayor id, etc.)
        name_by_nro[n] = str(nom) if nom is not None else name_by_nro.get(n, None)

    # 3) Saldos: traemos por id de descuento y los agregamos por nro_colegio
    all_desc_ids: List[int] = []
    for n in nro_list:
        all_desc_ids.extend(ids_by_nro.get(n, []))
    all_desc_ids = sorted(set(all_desc_ids))

    saldo_by_nro: Dict[int, Decimal] = {n: Decimal("0.00") for n in nro_list}
    if all_desc_ids:
        sal_rows = (await db.execute(
            select(DeduccionSaldo.concepto_id, DeduccionSaldo.saldo)
            .where(
                DeduccionSaldo.medico_id == medico_id,
                DeduccionSaldo.concepto_tipo == "desc",
                DeduccionSaldo.concepto_id.in_(all_desc_ids),
            )
        )).all()
        # map id -> nro
        id_to_nro: Dict[int, int] = {}
        for n, ids in ids_by_nro.items():
            for i in ids:
                id_to_nro[i] = n
        for cid, saldo in sal_rows:
            n = id_to_nro.get(int(cid))
            if n is not None:
                v = Decimal(str(saldo or 0)).quantize(Decimal("0.01"))
                saldo_by_nro[n] = (saldo_by_nro.get(n, Decimal("0.00")) + v).quantize(Decimal("0.01"))

    # 4) Aplicaciones: por todos los ids del grupo, agregadas por nro
    apps_by_nro: DefaultDict[int, List[ConceptoAplicacionOut]] = defaultdict(list)
    if all_desc_ids:
        DC, LR = DeduccionColegio, LiquidacionResumen
        apps = (await db.execute(
            select(
                DC.descuento_id, DC.resumen_id, LR.anio, LR.mes,
                DC.monto_aplicado, DC.porcentaje_aplicado, DC.created_at
            )
            .join(LR, LR.id == DC.resumen_id)
            .where(
                DC.medico_id == medico_id,
                DC.descuento_id.in_(all_desc_ids),
            )
            .order_by(desc(LR.anio), desc(LR.mes), DC.created_at.desc())
        )).all()

        id_to_nro: Dict[int, int] = {}
        for n, ids in ids_by_nro.items():
            for i in ids:
                id_to_nro[i] = n

        for descuento_id, resumen_id, anio, mes, monto, pct, created in apps:
            n = id_to_nro.get(int(descuento_id))
            if n is None:
                continue
            apps_by_nro[n].append(ConceptoAplicacionOut(
                resumen_id=int(resumen_id),
                periodo=f"{int(anio):04d}-{int(mes):02d}",
                created_at=created,
                monto_aplicado=Decimal(str(monto or 0)).quantize(Decimal("0.01")),
                porcentaje_aplicado=Decimal(str(pct or 0)).quantize(Decimal("0.01")),
            ))

    # 5) Salida en el orden del store
    out: List[MedicoConceptoOut] = []
    for n in nro_list:
        out.append(MedicoConceptoOut(
            concepto_tipo="desc",
            concepto_id=n,
            concepto_nro_colegio=n,
            concepto_nombre=name_by_nro.get(n),
            saldo=saldo_by_nro.get(n, Decimal("0.00")),
            aplicaciones=apps_by_nro.get(n, []),
        ))
    return out

@router.get("/{medico_id}/especialidades", response_model=List[MedicoEspecialidadOut])
async def listar_especialidades_medico(
    medico_id: int,
    db: AsyncSession = Depends(get_db),
):
    store = (await db.execute(
        select(ListadoMedico.conceps_espec).where(ListadoMedico.ID == medico_id)
    )).scalar_one_or_none() or {"conceps": [], "espec": []}

    espec_ids = [int(x) for x in (store.get("espec") or [])]
    if not espec_ids:
        return []

    rows = (await db.execute(
        select(Especialidad.ID, Especialidad.ESPECIALIDAD)
        .where(Especialidad.ID.in_(espec_ids))
    )).all()
    name_by_id = {int(r[0]): r[1] for r in rows}
    return [MedicoEspecialidadOut(id=eid, nombre=name_by_id.get(eid)) for eid in espec_ids]