from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from operator import and_
from pathlib import Path
import re
from sqlalchemy.orm.attributes import flag_modified

from typing import DefaultDict, Optional, Dict, List
from fastapi import APIRouter, Body, Depends, File, Form, Query, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import desc, func, select, or_, cast, String
from app.core.passwords import hash_password
from app.db.database import get_db
from app.db.models import (
    DeduccionColegio, Descuentos, DetalleLiquidacion, Documento, Especialidad, Liquidacion, ListadoMedico,
    DeduccionSaldo, DeduccionAplicacion, LiquidacionResumen, SolicitudRegistro
)
from app.schemas.deduccion_schema import CrearDeudaOut, NuevaDeudaIn
from app.schemas.medicos_schema import (
    AsociarConceptoIn, CEAppOut, CEBundleOut, CEBundlePatchIn, CEStoreOut, ConceptRecordOut, ConceptoAplicacionOut, DoctorStatsPointOut, MedicoConceptoOut, MedicoDebtOut, MedicoDocOut, MedicoEspecialidadOut, MedicoListRow, MedicoDetailOut, PatchCEIn
)
from app.auth.deps import require_scope
from app.schemas.registro_schema import RegisterIn, RegisterOut
from app.services.email import send_email_resend
from app.core.config import settings
from app.utils.main import _parse_date

router = APIRouter()

UPLOAD_DIR = Path(settings.UPLOAD_DIR)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@router.get("", response_model=List[MedicoListRow],dependencies=[Depends(require_scope("medicos:leer"))])
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
            ListadoMedico.MAIL_PARTICULAR.label("mail_particular"),
            ListadoMedico.TELE_PARTICULAR.label("tele_particular"),
            ListadoMedico.FECHA_INGRESO.label("fecha_ingreso"),
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
                cast(ListadoMedico.MAIL_PARTICULAR, String).ilike(pattern),

            )
        )

    rows = (await db.execute(stmt)).mappings().all()
    return [dict(r) for r in rows]

@router.get("/count", dependencies=[Depends(require_scope("medicos:leer"))])
async def contar_medicos(
    q: Optional[str] = Query(None, description="Buscar por nombre, nro socio, matrículas o documento"),
    db: AsyncSession = Depends(get_db),
):
    M = ListadoMedico
    stmt = select(func.count()).select_from(M).where(M.EXISTE == "S")
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                M.NOMBRE.ilike(like),
                cast(M.NRO_SOCIO, String).ilike(like),
                cast(M.MATRICULA_PROV, String).ilike(like),
                cast(M.DOCUMENTO, String).ilike(like),
            )
        )
    total = (await db.execute(stmt)).scalar_one() or 0
    return {"count": int(total)}

@router.get("/stats/monthly", dependencies=[Depends(require_scope("medicos:leer"))])
async def medicos_stats_monthly(
    desde: Optional[date] = Query(None, description="YYYY-MM-DD"),
    hasta: Optional[date] = Query(None, description="YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
):
    M = ListadoMedico
    cond = [M.EXISTE == "S", M.FECHA_INGRESO.is_not(None)]
    if desde: cond.append(M.FECHA_INGRESO >= desde)
    if hasta: cond.append(M.FECHA_INGRESO <= hasta)

    yr = func.extract("year", M.FECHA_INGRESO).label("year")
    mo = func.extract("month", M.FECHA_INGRESO).label("month")

    stmt = (
        select(yr, mo, func.count().label("count"))
        .where(and_(*cond))
        .group_by(yr, mo)
        .order_by(yr, mo)
    )
    rows = (await db.execute(stmt)).all()
    return [{"year": int(r.year), "month": int(r.month), "count": int(r.count)} for r in rows]

@router.post("/register", response_model=RegisterOut)
async def register_medico(body: RegisterIn, db: AsyncSession = Depends(get_db)):
    nombre = f"{(body.firstName or '').strip()} {(body.lastName or '').strip()}".strip()
    # helpers: parse optional ints and dates from strings coming from the public schema
    def _int_or_zero(v: Optional[str]) -> int:
        try:
            if v is None:
                return 0
            s = str(v).strip()
            return int(s) if s != "" else 0
        except Exception:
            return 0

    def _parse_date(s: Optional[str]):
        if not s:
            return None
        try:
            # accept ISO-like YYYY-MM-DD or full datetime
            if "T" in s:
                return datetime.fromisoformat(s).date()
            return date.fromisoformat(s)
        except Exception:
            try:
                # fallback: try parsing common formats
                return datetime.strptime(s, "%d/%m/%Y").date()
            except Exception:
                return None

    # map numeric licence fields
    matricula_prov = _int_or_zero(body.provincialLicense)
    matricula_nac = _int_or_zero(body.nationalLicense or body.matricula_nac if hasattr(body, 'matricula_nac') else None)

    fecha_matricula = (_parse_date(body.provincialLicenseDate)
                       or _parse_date(body.nationalLicenseDate)
                       or _parse_date(body.graduationDate))

    medico = ListadoMedico(
        # columnas MAYÚSCULAS ya existentes
        NOMBRE=nombre or "-",
        TIPO_DOC=body.documentType or "DNI",
        DOCUMENTO=str(body.documentNumber or "0"),
        DOMICILIO_PARTICULAR=body.address or "a",
        PROVINCIA=body.province or "A",
        CODIGO_POSTAL=body.postalCode or "0",
        TELE_PARTICULAR=body.phone or "0",
        CELULAR_PARTICULAR=body.mobile or "0",
        DOMICILIO_CONSULTA=body.officeAddress or "a",
        TELEFONO_CONSULTA=body.officePhone or "0",
        MAIL_PARTICULAR=body.email or "a",
        CUIT=body.cuit or "0",
        OBSERVACION=body.observations or "A",
        EXISTE="N",  # <-- clave
        ANSSAL=body.anssal,
        # matrícula / licencias
        MATRICULA_PROV=matricula_prov,
        MATRICULA_NAC=matricula_nac,
        FECHA_MATRICULA=fecha_matricula,
        # fechas y vencimientos
        FECHA_NAC=_parse_date(body.birthDate),
        VENCIMIENTO_ANSSAL=_parse_date(body.anssalExpiry),
        VENCIMIENTO_MALAPRAXIS=_parse_date(body.malpracticeExpiry),
        VENCIMIENTO_COBERTURA=_parse_date(body.coverageExpiry),
        FECHA_RECIBIDO = _parse_date(body.graduationDate),
        # cobertura numeric (si viene como número)
        COBERTURA=_int_or_zero(body.malpracticeCoverage),
        # texto fields
        MALAPRAXIS=body.malpracticeCompany or "A",
        nro_resolucion=body.resolutionNumber or None,
        fecha_resolucion=_parse_date(body.resolutionDate),
        # columnas snake_case nuevas (opcionales)
        apellido=body.lastName or None,
        nombre_=body.firstName or None,
        localidad=body.locality or None,
        cbu=body.cbu or None,
        condicion_impositiva=(body.condicionImpositiva or body.taxCondition) or None,
        titulo=body.specialty or None,
    )
    # password temporal: DNI; luego el usuario la debe cambiar
    pwd_raw = str(body.documentNumber or "")
    medico.hashed_password = hash_password(pwd_raw)


    db.add(medico)
    await db.commit()
    await db.refresh(medico)

    solicitud = SolicitudRegistro(
        estado="pendiente",
        medico_id=medico.ID,
        observaciones=None,
    )
    db.add(solicitud)
    await db.commit()
    await db.refresh(solicitud)

    # Mail de notificación
    try:
        send_email_resend(
            to=(settings.EMAIL_NOTIFY_TO),
            subject="Nueva solicitud de registro",
            html=f"""
              <h3>Nueva solicitud de registro</h3>
              <p><b>Médico:</b> {nombre}</p>
              <p><b>Documento:</b> {body.documentType} {body.documentNumber}</p>
              <p><b>Email:</b> {body.email or "-"}</p>
              <p>Solicitud #{solicitud.id} — Médico ID {medico.ID}</p>
            """,
        )
    except Exception as e:
        print("EMAIL_SEND_FAILED:", e)

    return {"medico_id": medico.ID, "solicitud_id": solicitud.id, "ok": True}


@router.get("/{medico_id}", response_model=MedicoDetailOut)
async def obtener_medico(
    medico_id: int,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(
        # ---- básicos ----
        ListadoMedico.ID.label("id"),
        ListadoMedico.NRO_SOCIO.label("nro_socio"),
        ListadoMedico.NOMBRE.label("nombre"),
        ListadoMedico.nombre_.label("nombre_"),
        ListadoMedico.apellido.label("apellido"),
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

        # ---- personales extra ----
        ListadoMedico.localidad.label("localidad"),
        ListadoMedico.DOMICILIO_PARTICULAR.label("domicilio_particular"),
        ListadoMedico.TELE_PARTICULAR.label("tele_particular"),
        ListadoMedico.CELULAR_PARTICULAR.label("celular_particular"),

        # ---- profesionales extra ----
        # OJO: si en tu modelo es MAYÚSCULA, usar ListadoMedico.TITULO
        ListadoMedico.titulo.label("titulo"),
        ListadoMedico.FECHA_RECIBIDO.label("fecha_recibido"),
        ListadoMedico.FECHA_MATRICULA.label("fecha_matricula"),
        # Si tu modelo las tiene en snake-case, así; si están en MAYÚSCULA, cambialas.
        ListadoMedico.nro_resolucion.label("nro_resolucion"),
        ListadoMedico.fecha_resolucion.label("fecha_resolucion"),

        # ---- impositivos ----
        ListadoMedico.condicion_impositiva.label("condicion_impositiva"),
        ListadoMedico.ANSSAL.label("anssal"),
        ListadoMedico.VENCIMIENTO_ANSSAL.label("vencimiento_anssal"),
        ListadoMedico.MALAPRAXIS.label("malapraxis"),
        ListadoMedico.VENCIMIENTO_MALAPRAXIS.label("vencimiento_malapraxis"),
        ListadoMedico.COBERTURA.label("cobertura"),
        ListadoMedico.VENCIMIENTO_COBERTURA.label("vencimiento_cobertura"),
        ListadoMedico.cbu.label("cbu"),
        ListadoMedico.OBSERVACION.label("observacion"),
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


LABEL_TO_FIELD = {
    "titulo":                 "attach_titulo",
    "matricula_nac":          "attach_matricula_nac",
    "matricula_nacional":     "attach_matricula_nac",
    "matricula_prov":         "attach_matricula_prov",
    "resolucion":             "attach_resolucion",
    "habilitacion_municipal": "attach_habilitacion_municipal",
    "cuit":                   "attach_cuit",
    "condicion_impositiva":   "attach_condicion_impositiva",
    "anssal":                 "attach_anssal",
    "malapraxis":             "attach_malapraxis",
    "cbu":                    "attach_cbu",
    "documento":              "attach_dni",
}

@router.post("/register/{medico_id}/document")
async def upload_document(
    medico_id: int,
    file: UploadFile = File(...),
    label: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
):
    med = await db.get(ListadoMedico, medico_id)
    if not med:
        raise HTTPException(404, "Médico no encontrado")

    folder = UPLOAD_DIR / "medicos" / str(medico_id)
    folder.mkdir(parents=True, exist_ok=True)

    safe_name = f"{int(datetime.now().timestamp())}_{(file.filename or 'doc').replace(' ','_')}"
    dest = folder / safe_name

    content = await file.read()
    dest.write_bytes(content)

    doc = Documento(
        medico_id = medico_id,
        label = label,
        original_name = file.filename or "",
        filename = safe_name,
        content_type = file.content_type or "application/octet-stream",
        size = len(content),
        path = str(dest),
    )
    db.add(doc)

    if label:
        field = LABEL_TO_FIELD.get(label.strip().lower())
        if field and hasattr(med, field):
            setattr(med, field, str(dest))

    await db.flush()         # 🔸 asegura PK
    await db.commit()
    await db.refresh(doc)    # 🔸 garantiza doc.id
    try:
        if label:
            lab = label.strip().lower()
            idx = None
            if lab == "resolucion":
                idx = 0
            else:
                m = re.match(r"resolucion_(\d+)$", lab)
                if m:
                    idx = int(m.group(1)) - 1  # <-- corrige off-by-one (0..5)

            if idx is not None:
                # construir antes de usar
                data = med.conceps_espec or {"conceps": [], "espec": []}
                espec = list(data.get("espec") or [])

                print("LABEL:", lab, "IDX:", idx, "LEN:", len(espec))

                if 0 <= idx < len(espec):
                    espec[idx]["adjunto"] = str(doc.id)  # o doc.filename / path
                    data["espec"] = espec
                    med.conceps_espec = data
                    flag_modified(med, "conceps_espec") # ← *** clave ***

                    await db.flush()
                    await db.commit()  # <-- asegurá persistencia
    except Exception as e:
        # te conviene loguearlo al menos mientras debugueás
        print("ADMIN_UPLOAD_EXC:", repr(e))
    return {"ok": True, "doc_id": doc.id}

@router.post("/admin/register",
             response_model=RegisterOut,
             dependencies=[Depends(require_scope("medicos:agregar"))])
async def admin_register_medico(body: RegisterIn,
                                db: AsyncSession = Depends(get_db)):
    nombre = f"{(body.firstName or '').strip()} {(body.lastName or '').strip()}".strip()
    def _int_or_zero(v: Optional[str]) -> int:
        try:
            if v is None:
                return 0
            s = str(v).strip()
            return int(s) if s != "" else 0
        except Exception:
            return 0


    # map numeric licence fields
    matricula_prov = _int_or_zero(body.provincialLicense)
    matricula_nac = _int_or_zero(body.nationalLicense or body.matricula_nac if hasattr(body, 'matricula_nac') else None)

    fecha_matricula = (_parse_date(body.provincialLicenseDate)
                       or _parse_date(body.nationalLicenseDate)
                       or _parse_date(body.graduationDate))
    # --- Especialidades -------------------------------------------------
    # Si vino specialties (nuevo), lo usamos. Si no, mapeamos desde los campos viejos.
    spec_items = []
    if body.specialties:
        # ya vienen con id_colegio_espe
        for it in (body.specialties or []):
            if not it or not getattr(it, "id_colegio_espe", None):
                continue
            spec_items.append({
                "id_colegio": int(it.id_colegio_espe),
                "n_resolucion": (it.n_resolucion or None),
                "fecha_resolucion": (_parse_date(it.fecha_resolucion) or None),
                "adjunto": (it.adjunto or None),
            })
    else:
        # retrocompat: specialty (id tabla), resolutionNumber, resolutionDate
        # Debemos traducir specialty(ID) -> ID_COLEGIO_ESPE
        id_tabla = None
        try:
            id_tabla = int(body.specialty) if body.specialty else None
        except Exception:
            id_tabla = None

        id_colegio = None
        if id_tabla:
            row = (await db.execute(select(Especialidad).where(Especialidad.ID == id_tabla))).scalar_one_or_none()
            if row:
                id_colegio = int(row.ID_COLEGIO_ESPE)

        if id_colegio:
            spec_items.append({
                "id_colegio": id_colegio,
                "n_resolucion": (body.resolutionNumber or None),
                "fecha_resolucion": (_parse_date(body.resolutionDate) or None),
                "adjunto": None,  # se actualizará al subir documento(s)
            })

    # Limitar a 6 como máximo
    spec_items = spec_items[:6]

    # Para NRO_ESPECIALIDAD* necesitamos SOLO los ID_COLEGIO_ESPE
    nro_especs = [int(x["id_colegio"]) for x in spec_items]
    while len(nro_especs) < 6:
        nro_especs.append(0)  # completar con 0
        
    medico = ListadoMedico(
        # columnas MAYÚSCULAS ya existentes
        NOMBRE=nombre or "-",
        TIPO_DOC=body.documentType or "DNI",
        DOCUMENTO=str(body.documentNumber or "0"),
        DOMICILIO_PARTICULAR=body.address or "a",
        PROVINCIA=body.province or "A",
        CODIGO_POSTAL=body.postalCode or "0",
        TELE_PARTICULAR=body.phone or "0",
        CELULAR_PARTICULAR=body.mobile or "0",
        DOMICILIO_CONSULTA=body.officeAddress or "a",
        TELEFONO_CONSULTA=body.officePhone or "0",
        MAIL_PARTICULAR=body.email or "a",
        CUIT=body.cuit or "0",
        OBSERVACION=body.observations or "A",
        EXISTE="N",  # <-- clave
        ANSSAL=_int_or_zero(body.anssal),
        NRO_ESPECIALIDAD = nro_especs[0],
        NRO_ESPECIALIDAD2 = nro_especs[1],
        NRO_ESPECIALIDAD3 = nro_especs[2],
        NRO_ESPECIALIDAD4 = nro_especs[3],
        NRO_ESPECIALIDAD5 = nro_especs[4],
        NRO_ESPECIALIDAD6 = nro_especs[5],
        # matrícula / licencias
        MATRICULA_PROV=matricula_prov,
        MATRICULA_NAC=matricula_nac,
        FECHA_MATRICULA=fecha_matricula,
        # fechas y vencimientos
        FECHA_NAC=_parse_date(body.birthDate),
        VENCIMIENTO_ANSSAL=_parse_date(body.anssalExpiry),
        VENCIMIENTO_MALAPRAXIS=_parse_date(body.malpracticeExpiry),
        VENCIMIENTO_COBERTURA=_parse_date(body.coverageExpiry),
        FECHA_RECIBIDO = _parse_date(body.graduationDate),
        # cobertura numeric (si viene como número)
        COBERTURA=_int_or_zero(body.malpracticeCoverage),
        # texto fields
        MALAPRAXIS=body.malpracticeCompany or "A",
        nro_resolucion=body.resolutionNumber or None,
        fecha_resolucion=_parse_date(body.resolutionDate),
        # columnas snake_case nuevas (opcionales)
        apellido=body.lastName or None,
        nombre_=body.firstName or None,
        localidad=body.locality or None,
        cbu=body.cbu or None,
        condicion_impositiva=(body.condicionImpositiva or body.taxCondition) or None,
        titulo=body.specialty or None,
    )
    # password temporal: DNI; luego el usuario la debe cambiar
    pwd_raw = str(body.documentNumber or "")
    medico.hashed_password = hash_password(pwd_raw)

    medico.conceps_espec = {
        "conceps": [],         # no tocamos acá
        "espec": [
            {
                "id_colegio": it["id_colegio"],
                "n_resolucion": it["n_resolucion"],
                "fecha_resolucion": (it["fecha_resolucion"].isoformat() if it["fecha_resolucion"] else None),
                "adjunto": it["adjunto"],  # luego se podrá actualizar cuando subas el archivo
            }
            for it in spec_items
        ]
    }

    db.add(medico)
    await db.commit()
    await db.refresh(medico)

    solicitud = SolicitudRegistro(
        estado="pendiente",
        medico_id=medico.ID,
        observaciones=None,
    )
    db.add(solicitud)
    await db.commit()
    await db.refresh(solicitud)

    # Mail de notificación
    try:
        send_email_resend(
            to=(settings.EMAIL_NOTIFY_TO),
            subject="Nueva solicitud de registro",
            html=f"""
              <h3>Nueva solicitud de registro</h3>
              <p><b>Médico:</b> {nombre}</p>
              <p><b>Documento:</b> {body.documentType} {body.documentNumber}</p>
              <p><b>Email:</b> {body.email or "-"}</p>
              <p>Solicitud #{solicitud.id} — Médico ID {medico.ID}</p>
            """,
        )
    except Exception as e:
        print("EMAIL_SEND_FAILED:", e)

    return {"medico_id": medico.ID, "solicitud_id": solicitud.id, "ok": True}


@router.post("/admin/register/{medico_id}/document",
             dependencies=[Depends(require_scope("medicos:agregar"))])
async def admin_upload_document(medico_id: int,
                                file: UploadFile = File(...),
                                label: Optional[str] = Form(None),
                                db: AsyncSession = Depends(get_db)):
    med = await db.get(ListadoMedico, medico_id)
    if not med:
        raise HTTPException(404, "Médico no encontrado")

    folder = UPLOAD_DIR / "medicos" / str(medico_id)
    folder.mkdir(parents=True, exist_ok=True)

    safe_name = f"{int(datetime.now().timestamp())}_{(file.filename or 'doc').replace(' ','_')}"
    dest = folder / safe_name

    content = await file.read()
    dest.write_bytes(content)

    doc = Documento(
        medico_id = medico_id,
        label = label,
        original_name = file.filename or "",
        filename = safe_name,
        content_type = file.content_type or "application/octet-stream",
        size = len(content),
        path = str(dest),
    )
    db.add(doc)

    if label:
        field = LABEL_TO_FIELD.get(label.strip().lower())
        if field and hasattr(med, field):
            setattr(med, field, str(dest))

    await db.flush()         # 🔸 asegura PK
    await db.commit()
    await db.refresh(doc)    # 🔸 garantiza doc.id
    try:
        if label:
            lab = label.strip().lower()
            idx = None
            if lab == "resolucion":
                idx = 0
            else:
                m = re.match(r"resolucion_(\d+)$", lab)
                if m:
                    idx = int(m.group(1)) - 1  # <-- corrige off-by-one (0..5)

            if idx is not None:
                # construir antes de usar
                data = med.conceps_espec or {"conceps": [], "espec": []}
                espec = list(data.get("espec") or [])

                print("LABEL:", lab, "IDX:", idx, "LEN:", len(espec))

                if 0 <= idx < len(espec):
                    espec[idx]["adjunto"] = str(doc.id)  # o doc.filename / path
                    data["espec"] = espec
                    med.conceps_espec = data
                    flag_modified(med, "conceps_espec") # ← *** clave ***
                    await db.flush()
                    await db.commit()  # <-- asegurá persistencia
    except Exception as e:
        # te conviene loguearlo al menos mientras debugueás
        print("ADMIN_UPLOAD_EXC:", repr(e))
    return {"ok": True, "doc_id": doc.id}

