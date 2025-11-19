from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
import json
from operator import and_
import os
from pathlib import Path
import re
import shutil
from sqlalchemy.exc import SQLAlchemyError
# import aiofiles
from sqlalchemy.orm.attributes import flag_modified
from app.utils.main import (
    SPECIALTY_SLOTS, _parse_conceps_espec, _dump_conceps_espec,
    _find_slot_index, _next_free_slot_index, _parse_fecha_to_yyyy_mm_dd, build_espec_item, parse_conceps_espec, parse_ddmmyyyy,save_upload_for_medico
)

from typing import Any, DefaultDict, Literal, Optional, Dict, List
from fastapi import APIRouter, Body, Depends, File, Form, Query, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import case, delete, desc, func, literal, select, or_, cast, String, update
from app.core.passwords import hash_password
from app.db.database import get_db
from app.db.models import (
    DeduccionColegio, Descuentos, DetalleLiquidacion, Documento, Especialidad, Liquidacion, ListadoMedico,
    DeduccionSaldo, DeduccionAplicacion, LiquidacionResumen, SolicitudRegistro
)
from app.schemas.deduccion_schema import CrearDeudaOut, NuevaDeudaIn
from app.schemas.medicos_schema import (
    AsignarEspecialidadIn, AsociarConceptoIn, CEAppOut, CEBundleOut, CEBundlePatchIn, CEStoreOut, ConceptRecordOut, ConceptoAplicacionOut, DoctorStatsPointOut, MedicoConceptoOut, MedicoDebtOut, MedicoDocOut, MedicoEspecialidadOut, MedicoListRow, MedicoDetailOut, MedicoUpdateIn, MedicoUpdateOut, PatchCEIn
)
from app.auth.deps import require_scope
from app.schemas.registro_schema import RegisterIn, RegisterOut
from app.services.email import send_email_resend
from app.core.config import settings
from app.utils.main import _parse_date
from app.services.medicos_register_service import create_medico_and_solicitud

router = APIRouter()

MEDIA_URL = Path(settings.MEDIA_URL)
MEDIA_URL.mkdir(parents=True, exist_ok=True)

def _parse_date_or_none(s: str | None) -> date | None:
    if not s:
        return None
    try:
        # formato "YYYY-MM-DD"
        return date.fromisoformat(s)
    except Exception:
        return None

@router.get(
    "",
    response_model=List[MedicoListRow],
    dependencies=[Depends(require_scope("medicos:leer"))],
)
async def listar_medicos(
    db: AsyncSession = Depends(get_db),
    q: Optional[str] = Query(None, description="Buscar por nombre, nro socio o matrículas"),
    estado: Literal["todos", "activos", "inactivos"] = Query("todos"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    # fecha ingreso como texto normalizado
    fecha_str = func.nullif(
        func.date_format(ListadoMedico.FECHA_INGRESO, "%Y-%m-%d"),
        "0000-00-00"
    ).label("fecha_ingreso")

    # documento como texto
    doc_str = cast(ListadoMedico.DOCUMENTO, String).label("documento")

    # booleano "activo" calculado en SQL (TRIM + UPPER para datos legacy)
    activo_expr = case(
        (func.upper(func.trim(ListadoMedico.EXISTE)) == literal("S"), literal(1)),
        else_=literal(0),
    ).label("activo")
    nro_expr = func.nullif(ListadoMedico.NRO_SOCIO, 0).label("nro_socio")
    # opcional: traer también el valor crudo por si lo querés ver
    existe_raw = func.trim(ListadoMedico.EXISTE).label("existe")

    stmt = (
        select(
            ListadoMedico.ID.label("id"),
            nro_expr,
            ListadoMedico.NOMBRE.label("nombre"),
            ListadoMedico.MATRICULA_PROV.label("matricula_prov"),
            doc_str,
            ListadoMedico.MAIL_PARTICULAR.label("mail_particular"),
            ListadoMedico.TELE_PARTICULAR.label("tele_particular"),
            fecha_str,
            activo_expr,
            existe_raw,
        )
        .order_by(ListadoMedico.NOMBRE.asc())
        .offset(skip)
        .limit(limit)
    )

    if estado == "activos":
        stmt = stmt.where(func.upper(func.trim(ListadoMedico.EXISTE)) == "S")
    elif estado == "inactivos":
        stmt = stmt.where(func.upper(func.trim(ListadoMedico.EXISTE)) != "S")
    # "todos" => sin filtro

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

    # saneo mínimo (ya veníamos usando esto para fecha/doc)
    out = []
    for r in rows:
        d = dict(r)

        # —— normalizar nro_socio -> int | None ——
        ns = d.get("nro_socio")
        if ns in (None, "", "0", 0):
            d["nro_socio"] = None
        else:
            try:
                d["nro_socio"] = int(ns)
            except Exception:
                d["nro_socio"] = None

        # —— normalizar documento (como ya tenías) ——
        v = d.get("documento")
        v = None if v is None else str(v).strip()
        if v in ("", "0"):
            v = None
        d["documento"] = v

        # —— normalizar fecha_ingreso (como ya tenías) ——
        f = d.get("fecha_ingreso")
        if f is not None:
            f = str(f).strip()
            if not f or f.startswith("0000"):
                f = None
            else:
                f = f[:10]
        d["fecha_ingreso"] = f

        out.append(d)

    return out


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

# Registro "publico" ========================================================================

@router.post("/register", response_model=RegisterOut)
async def public_register_medico(body: RegisterIn, db: AsyncSession = Depends(get_db)):
    med, sol = await create_medico_and_solicitud(db, body, existe="N")
    # (si quieres, aquí va el mail de notificación)
    return {"medico_id": med.ID, "solicitud_id": sol.id, "ok": True}


# Registro "privado" con permisos de admin =============================================

@router.post("/admin/register",response_model=RegisterOut, dependencies=[Depends(require_scope("medicos:agregar"))])
async def admin_register_medico(body: RegisterIn, db: AsyncSession = Depends(get_db)):
    med, sol = await create_medico_and_solicitud(db, body, existe="N")
    # (si quieres, aquí va el mail de notificación admin)
    return {"medico_id": med.ID, "solicitud_id": sol.id, "ok": True}
  

@router.get("/{medico_id}", response_model=MedicoDetailOut)
async def obtener_medico(
    medico_id: int,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(
            # --- básicos ---
            ListadoMedico.ID.label("id"),
            ListadoMedico.NRO_SOCIO.label("nro_socio"),
            ListadoMedico.NOMBRE.label("name"),
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

            # --- personales extra ---
            ListadoMedico.localidad.label("localidad"),
            ListadoMedico.DOMICILIO_PARTICULAR.label("domicilio_particular"),
            ListadoMedico.TELE_PARTICULAR.label("tele_particular"),
            ListadoMedico.CELULAR_PARTICULAR.label("celular_particular"),

            # --- profesionales extra ---
            ListadoMedico.titulo.label("titulo"),
            ListadoMedico.FECHA_RECIBIDO.label("fecha_recibido"),
            ListadoMedico.FECHA_MATRICULA.label("fecha_matricula"),
            ListadoMedico.nro_resolucion.label("nro_resolucion"),
            ListadoMedico.fecha_resolucion.label("fecha_resolucion"),
            ListadoMedico.conceps_espec.label("conceps_espec"),

            # --- impositivos ---
            ListadoMedico.condicion_impositiva.label("condicion_impositiva"),
            ListadoMedico.ANSSAL.label("anssal"),
            ListadoMedico.VENCIMIENTO_ANSSAL.label("vencimiento_anssal"),
            ListadoMedico.MALAPRAXIS.label("malapraxis"),
            ListadoMedico.VENCIMIENTO_MALAPRAXIS.label("vencimiento_malapraxis"),
            ListadoMedico.COBERTURA.label("cobertura"),
            ListadoMedico.VENCIMIENTO_COBERTURA.label("vencimiento_cobertura"),
            ListadoMedico.cbu.label("cbu"),
            ListadoMedico.OBSERVACION.label("observacion"),

            # --- adjuntos ---
            ListadoMedico.attach_titulo.label("attach_titulo"),
            ListadoMedico.attach_matricula_nac.label("attach_matricula_nac"),
            ListadoMedico.attach_matricula_prov.label("attach_matricula_prov"),
            ListadoMedico.attach_resolucion.label("attach_resolucion"),
            ListadoMedico.attach_habilitacion_municipal.label("attach_habilitacion_municipal"),
            ListadoMedico.attach_cuit.label("attach_cuit"),
            ListadoMedico.attach_condicion_impositiva.label("attach_condicion_impositiva"),
            ListadoMedico.attach_anssal.label("attach_anssal"),
            ListadoMedico.attach_malapraxis.label("attach_malapraxis"),
            ListadoMedico.attach_cbu.label("attach_cbu"),
            ListadoMedico.attach_dni.label("attach_dni"),
        )
        .where(ListadoMedico.ID == medico_id)
    )

    result = await db.execute(stmt)
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Médico no encontrado")

    d = dict(row)

    # ---- helpers ----
    def norm_path(p: str | None) -> str | None:
        if not p:
            return None
        s = str(p).strip()
        if not s:
            return None
        if s.startswith("http://") or s.startswith("https://"):
            return s
        # forzamos a ruta pública absoluta (desde backend)
        if not s.startswith("/"):
            s = "/" + s
        return s

    # ---- construir especialidades desde conceps_espec.espec ----
    raw = d.get("conceps_espec") or {}
    espec_list = raw.get("espec") or []

    # Colectar IDs de adjuntos (que son números) e IDs de especialidad (id_colegio)
    adj_ids: set[int] = set()
    espec_ids: set[int] = set()
    for it in espec_list:
        adj = it.get("adjunto")
        if adj is not None:
            s = str(adj).strip()
            if s.isdigit():
                adj_ids.add(int(s))
        id_col = it.get("id_colegio")
        if id_col is not None:
            try:
                espec_ids.add(int(id_col))
            except Exception:
                pass

    # --- Lookup masivo a Documentos -> obtener path por id ---
    doc_path_by_id: dict[int, str] = {}
    if adj_ids:
        qdocs = await db.execute(select(Documento.id, Documento.path).where(Documento.id.in_(adj_ids)))
        for did, path in qdocs.all():
            if path:
                doc_path_by_id[int(did)] = str(path)

    # --- Lookup masivo a Especialidad -> obtener nombre por id ---
    espec_nombre_by_id: dict[int, str] = {}
    if espec_ids:
        qesp = await db.execute(select(Especialidad.ID, Especialidad.ID_COLEGIO_ESPE, Especialidad.ESPECIALIDAD).where(Especialidad.ID_COLEGIO_ESPE.in_(espec_ids)))
        for eid, id_colegio, nombre in qesp.all():
            if nombre:
                espec_nombre_by_id[int(id_colegio)] = str(nombre)

    especialidades = []
    for it in espec_list:
        id_col = it.get("id_colegio")
        n_res = it.get("n_resolucion")
        f_res = it.get("fecha_resolucion")
        adj = it.get("adjunto")

        # resolver adjunto_url:
        adj_url: str | None = None
        if adj is not None:
            s = str(adj).strip()
            if s.isdigit():
                # ID de documento -> usar path real
                did = int(s)
                adj_url = norm_path(doc_path_by_id.get(did))
            else:
                # por si adj ya es ruta
                if s.startswith("/") or s.startswith("uploads/") or s.startswith("http"):
                    adj_url = norm_path(s)

        # resolver nombre de especialidad:
        espec_nombre = None
        try:
            espec_nombre = espec_nombre_by_id.get(int(id_col)) if id_col is not None else None
        except Exception:
            espec_nombre = None

        # label "id_colegio - nombre"
        if id_col is not None and espec_nombre:
            id_colegio_label = f"{id_col} - {espec_nombre}"
        elif id_col is not None:
            id_colegio_label = f"{id_col}"
        else:
            id_colegio_label = None

        especialidades.append(
            {
                "id_colegio": id_col,
                "n_resolucion": n_res,
                "fecha_resolucion": f_res,
                "adjunto": (str(adj) if adj is not None else None),
                "adjunto_url": adj_url,  # ← ahora sale del path real del Documento
                # extras útiles para el front:
                "especialidad_nombre": espec_nombre,
                "id_colegio_label": id_colegio_label,
            }
        )
   


    d["especialidades"] = especialidades
    d.pop("conceps_espec", None)  # ocultamos el JSON crudo

    return d

@router.put("/{medico_id}", response_model=MedicoDetailOut)
async def update_medico(
    medico_id: int,
    payload: MedicoUpdateIn = Body(default={}),   # ← clave: default={}
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(ListadoMedico, medico_id)
    if not row:
        raise HTTPException(404, "Médico no encontrado")
    
    data = payload.model_dump(exclude_unset=True)
    # (si querés ver qué llegó validado)
    print("📝 PATCH payload normalizado:", data)

    # normalización de fechas → date (tu código actual)
    for k in [
        "fecha_nac","fecha_recibido","fecha_matricula","fecha_resolucion",
        "vencimiento_anssal","vencimiento_malapraxis","vencimiento_cobertura",
    ]:
        if k in data:
            data[k] = _parse_date_or_none(data[k])

    for k, v in data.items():
        if hasattr(row, k):
            setattr(row, k, v)

    await db.flush()
    await db.commit()
    await db.refresh(row)
    return MedicoUpdateOut.model_validate(row, from_attributes=True)


# Deudas de medicos ===========================================

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

# ===========================================================================

#region Especialidades de medicos =========================================================
@router.get("/{medico_id}/especialidades", response_model=List[MedicoEspecialidadOut])
async def listar_especialidades_medico(
    medico_id: int,
    db: AsyncSession = Depends(get_db),
):
    # 1) Traer el JSON completo
    row = (
        await db.execute(
            select(ListadoMedico.conceps_espec).where(ListadoMedico.ID == medico_id)
        )
    ).scalar_one_or_none()

    raw = (row or {}) if isinstance(row, dict) else {}
    items = list(raw.get("espec") or [])


    # 2) Normalizar: puede venir como [47, 106, ...] o como
    # [{"id_colegio":47,"n_resolucion":"...","fecha_resolucion":"...","adjunto":"24"}, ...]
    ids: set[int] = set()
    extras: dict[int, dict] = {}

    for it in items:
        if isinstance(it, dict):
            try:
                eid = int(it.get("id_colegio"))
            except Exception:
                print("error?")
                continue
            ids.add(eid)
            extras[eid] = {
                "n_resolucion": (it.get("n_resolucion") or None),
                "fecha_resolucion": (it.get("fecha_resolucion") or None),
                "adjunto_id": int(it["adjunto"]) if str(it.get("adjunto", "")).strip().isdigit() else None,
            }
        else:
            # entero "puro"
            try:
                eid = int(it)
                ids.add(eid)
            except Exception:
                continue

    if not ids:
        return []

    # 3) Traer nombres de especialidades
    espec_rows = (
        await db.execute(
            select(Especialidad.ID_COLEGIO_ESPE, Especialidad.ESPECIALIDAD)
            .where(Especialidad.ID_COLEGIO_ESPE.in_(ids))
        )
    ).all()
    name_map = {int(r[0]): (r[1] or None) for r in espec_rows}

    # 4) Si hay adjuntos, traer paths en un solo query
    doc_ids = [e["adjunto_id"] for e in extras.values() if e.get("adjunto_id")]
    path_map: dict[int, str] = {}
    if doc_ids:
        docs = (
            await db.execute(
                select(Documento.id, Documento.path).where(Documento.id.in_(doc_ids))
            )
        ).all()
        for did, p in docs:
            if p:
                # el path ya arranca en 'uploads/...', entonces la URL pública es f"/{p}"
                path_map[int(did)] = f"/{str(p).lstrip('/')}"
    
    # 5) Armar respuesta
    out: list[dict] = []
    for eid in sorted(ids):
        ex = extras.get(eid, {})
        adj_id = ex.get("adjunto_id")
        out.append({
            "id": eid,
            "nombre": name_map.get(eid),
            "n_resolucion": ex.get("n_resolucion"),
            "fecha_resolucion": ex.get("fecha_resolucion"),
            "adjunto_id": adj_id,
            "adjunto_url": path_map.get(adj_id) if adj_id else None,
        })

    return out

def pretty_label_base(code: str) -> str:
    c = (code or "").strip().lower()
    mapping = {
        "documento": "Documento de identidad",
        "titulo": "Título",
        "matricula_nac": "Matrícula Nacional",
        "matricula_nacional": "Matrícula Nacional",
        "matricula_prov": "Matrícula Provincial",
        "habilitacion_municipal": "Habilitación municipal",
        "cuit": "CUIT",
        "condicion_impositiva": "Condición impositiva",
        "anssal": "ANSSAL",
        "malapraxis": "Malapraxis",
        "cbu": "CBU",
        "resolucion": "Adjunto de especialidad",
    }
    return mapping.get(c, code.replace("_", " ").title())


#region Utils para especialidades
def _ensure_medico(row):
    if not row:
        raise HTTPException(404, "Médico no encontrado")

async def _lock_medico(db: AsyncSession, medico_id: int):
    return (
        await db.execute(
            select(ListadoMedico).where(ListadoMedico.ID == medico_id).with_for_update()
        )
    ).scalars().first()

async def _check_especialidad_exists(db: AsyncSession, id_colegio: int):
    ok = (
        await db.execute(
            select(Especialidad.ID_COLEGIO_ESPE).where(
                Especialidad.ID_COLEGIO_ESPE == id_colegio
            )
        )
    ).first()
    if not ok:
        raise HTTPException(400, "Especialidad inexistente")

async def _label_old_doc_null(db: AsyncSession, medico_id: int, adj_id: Optional[int]):
    if adj_id:
        await db.execute(
            update(Documento)
            .where(Documento.id == adj_id, Documento.medico_id == medico_id)
            .values(label=None)
        )

async def _label_new_doc(db: AsyncSession, medico_id: int, adj_id: Optional[int], slot_idx: int):
    if adj_id:
        await db.execute(
            update(Documento)
            .where(Documento.id == adj_id, Documento.medico_id == medico_id)
            .values(label=f"resolucion_{slot_idx + 1}")
        )
#endregion

# Agregar la especialidad del medico
@router.post("/{medico_id}/especialidades", status_code=status.HTTP_204_NO_CONTENT)
async def add_especialidad(
    medico_id: int,
    payload: dict = Body(...),  # { id_colegio:int, n_resolucion?, fecha_resolucion?, adjunto_id? }
    db: AsyncSession = Depends(get_db),
):
    id_colegio = int(payload["id_colegio"])
    n_res = payload.get("n_resolucion")
    f_res = payload.get("fecha_resolucion")
    adj_id = payload.get("adjunto_id")

    row = await _lock_medico(db, medico_id)
    _ensure_medico(row)
    await _check_especialidad_exists(db, id_colegio)

    obj = parse_conceps_espec(row.conceps_espec)
    espec = obj["espec"]

    # ya está?
    if any(int(e.get("id_colegio")) == id_colegio for e in espec if isinstance(e, dict)):
        raise HTTPException(409, "La especialidad ya está asociada")

    # agregar
    item = build_espec_item(id_colegio, n_res, f_res, adj_id)
    espec.append(item)

    # asegurar slot
    slot_idx = _find_slot_index(row, id_colegio)
    if slot_idx is None:
        slot_idx = _next_free_slot_index(row)
        if slot_idx is None:
            raise HTTPException(409, "No hay más slots de especialidad")
        setattr(row, SPECIALTY_SLOTS[slot_idx], id_colegio)

    # etiquetar doc si corresponde
    await _label_new_doc(db, medico_id, int(adj_id) if (adj_id and str(adj_id).isdigit()) else None, slot_idx)

    row.conceps_espec = obj
    flag_modified(row, "conceps_espec")  
    await db.flush()                      
    await db.commit()

# Editar la especialidad del medico
@router.patch("/{medico_id}/especialidades/{id_colegio}", status_code=status.HTTP_204_NO_CONTENT)
async def edit_especialidad(
    medico_id: int,
    id_colegio: int,
    payload: dict = Body(...),  # { n_resolucion?, fecha_resolucion?, adjunto_id? }
    db: AsyncSession = Depends(get_db),
):
    n_res = payload.get("n_resolucion")
    f_res = payload.get("fecha_resolucion")
    new_adj = payload.get("adjunto_id")

    row = await _lock_medico(db, medico_id)
    _ensure_medico(row)

    obj = parse_conceps_espec(row.conceps_espec)
    espec = obj["espec"]

    idx = next((i for i, e in enumerate(espec) if isinstance(e, dict) and int(e.get("id_colegio")) == id_colegio), None)
    if idx is None:
        raise HTTPException(404, "La especialidad no está asociada")

    cur = espec[idx]
    old_adj = cur.get("adjunto")
    if n_res is not None:
        cur["n_resolucion"] = n_res
    if f_res is not None:
        cur["fecha_resolucion"] = f_res
    if new_adj is not None:
        # limpiar label viejo si cambia
        if old_adj and str(old_adj).isdigit() and str(new_adj) != str(old_adj):
            await _label_old_doc_null(db, medico_id, int(old_adj))
        cur["adjunto"] = str(new_adj)

        # volver a etiquetar el nuevo
        slot_idx = _find_slot_index(row, id_colegio)
        if slot_idx is not None:
            await _label_new_doc(db, medico_id, int(new_adj) if str(new_adj).isdigit() else None, slot_idx)

    row.conceps_espec = obj
    flag_modified(row, "conceps_espec")  
    await db.flush()  
    await db.commit()

# Eliminar la especialidad del medico
@router.delete("/{medico_id}/especialidades/{id_colegio}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_especialidad(
    medico_id: int,
    id_colegio: int,
    db: AsyncSession = Depends(get_db),
):
    row = await _lock_medico(db, medico_id)
    _ensure_medico(row)

    obj = parse_conceps_espec(row.conceps_espec)
    espec = obj["espec"]

    idx = next((i for i, e in enumerate(espec) if isinstance(e, dict) and int(e.get("id_colegio")) == id_colegio), None)
    if idx is None:
        # idempotente
        await db.commit()
        return

    # === 1) limpiar label adjunto si había
    old_adj = espec[idx].get("adjunto")
    old_adj_id: int | None = None
    if old_adj and str(old_adj).isdigit():
        old_adj_id = int(old_adj)
        await _label_old_doc_null(db, medico_id, old_adj_id)

    # === 2) quitar la especialidad del JSON
    espec.pop(idx)

    # === 3) liberar slot si estaba ocupado
    slot_idx = _find_slot_index(row, id_colegio)
    if slot_idx is not None:
        setattr(row, SPECIALTY_SLOTS[slot_idx], None)

    row.conceps_espec = obj
    flag_modified(row, "conceps_espec")

    # === 4) si había adjunto, verificar si está referenciado por otra especialidad
    file_path_to_delete: Path | None = None
    if old_adj_id is not None:
        still_used = any(
            (isinstance(e, dict) and str(e.get("adjunto", "")).strip() == str(old_adj_id))
            for e in espec
        )
        if not still_used:
            # buscar path en DB y borrar el registro
            res = await db.execute(
                select(Documento.path).where(
                    Documento.id == old_adj_id,
                    Documento.medico_id == medico_id,
                )
            )
            row_doc = res.first()
            if row_doc and row_doc[0]:
                file_path_to_delete = Path(str(row_doc[0]))

            await db.execute(
                delete(Documento).where(
                    Documento.id == old_adj_id,
                    Documento.medico_id == medico_id,
                )
            )

    await db.flush()
    await db.commit()

    # === 5) fuera de la transacción, intentar borrar el archivo físico
    try:
        if file_path_to_delete and file_path_to_delete.exists():
            file_path_to_delete.unlink(missing_ok=True)
    except Exception:
        # opcional: logueá si querés
        pass
#endregion 

@router.get("/{medico_id}/documentos", response_model=List[MedicoDocOut])
async def documentos_medico(
    medico_id: int,
    db: AsyncSession = Depends(get_db),
):
    # 1) Traer todos los documentos del médico
    doc_rows = (
        await db.execute(
            select(
                Documento.id,
                Documento.label,
                Documento.original_name,
                Documento.path,
                Documento.content_type,
                Documento.size,
            )
            .where(Documento.medico_id == medico_id)
            .order_by(Documento.created_at.desc(), Documento.id.desc())
        )
    ).all()

    # 2) Mapa adjunto_id -> id_colegio (desde conceps_espec)
    ce_raw = (
        await db.execute(
            select(ListadoMedico.conceps_espec).where(ListadoMedico.ID == medico_id)
        )
    ).scalar_one_or_none()

    espec_by_adjunto: Dict[int, int] = {}
    if ce_raw:
        try:
            if isinstance(ce_raw, (bytes, bytearray, memoryview)):
                ce_obj = json.loads(bytes(ce_raw).decode("utf-8", errors="ignore"))
            elif isinstance(ce_raw, str):
                ce_obj = json.loads(ce_raw) if ce_raw.strip() else {}
            elif isinstance(ce_raw, dict):
                ce_obj = ce_raw
            else:
                ce_obj = {}
        except Exception:
            ce_obj = {}
        for it in ce_obj.get("espec") or []:
            if not isinstance(it, dict):
                continue
            adj = it.get("adjunto")
            if adj is None:
                continue
            try:
                adj_id = int(str(adj).strip())
                id_col = int(str(it.get("id_colegio")).strip())
                espec_by_adjunto[adj_id] = id_col
            except Exception:
                continue

    # 3) Si hay adjuntos mapeados a especialidad, traer nombres
    espec_names: Dict[int, str] = {}
    id_colegio_set = set(espec_by_adjunto.values())
    if id_colegio_set:
        rows = (
            await db.execute(
                select(Especialidad.ID_COLEGIO_ESPE, Especialidad.ESPECIALIDAD)
                .where(Especialidad.ID_COLEGIO_ESPE.in_(id_colegio_set))
            )
        ).all()
        espec_names = {int(r[0]): (r[1] or "") for r in rows}

    # 4) Construir salida
    out: List[MedicoDocOut] = []
    for did, label, original_name, path, ctype, size in doc_rows:
        base_pretty = pretty_label_base(label or "")
        pretty = base_pretty

        # Si es resolucion/resolucion_n => usar el nombre de la especialidad
        if (label or "").lower().startswith("resolucion"):
            espec_id = espec_by_adjunto.get(int(did))
            if espec_id:
                espec_name = espec_names.get(espec_id) or f"ID {espec_id}"
                pretty = f"Adjunto {espec_name}"

        url = "/" + str(path).lstrip("/")  # asumiendo que servís /uploads

        out.append(
            MedicoDocOut(
                id=int(did),
                label=str(label or ""),
                pretty_label=pretty,
                file_name=str(original_name or ""),
                url=url,
                content_type=ctype,
                size=size,
            )
        )

    return out


def _safe_name(filename: str) -> str:
    # evita paths raros, y limpia separadores
    base = os.path.basename(filename or "")
    return base.replace("/", "_").replace("\\", "_")

async def _save_upload_for_medico(medico_id: int, up: UploadFile) -> dict:
    """
    Guarda el archivo físico en MEDIA_ROOT/medicos/{medico_id}/
    y retorna dict con: safe_name, rel_path (público), size, content_type, abs_path
    """
    if not up or not up.filename:
        raise HTTPException(400, "Archivo no recibido")

    folder_fs = MEDIA_URL / "medicos" / str(medico_id)
    folder_fs.mkdir(parents=True, exist_ok=True)

    safe_name = _safe_name(up.filename)
    abs_path = folder_fs / safe_name

    content = await up.read()
    abs_path.write_bytes(content)

    rel_path = f"{MEDIA_URL}/medicos/{medico_id}/{safe_name}"  # lo que exponés públicamente

    return {
        "safe_name": safe_name,
        "rel_path": rel_path,                  # ej: uploads/medicos/2446/xxx.pdf
        "size": len(content),
        "content_type": up.content_type or "application/octet-stream",
        "abs_path": abs_path,                  # Path en disco
        "original_name": up.filename or "",
    }

@router.post("/{medico_id}/documentos")
async def upload_documento_medico(
    medico_id: int,
    file: UploadFile | None = File(None),         # el front manda "file"
    adjunto: UploadFile | None = File(None),      # opcional si alguna vez mandás "adjunto"
    label: str | None = Form(None),               # opcional, por si querés etiquetar
    db: AsyncSession = Depends(get_db),
):
    up = file or adjunto
    if up is None:
        raise HTTPException(400, "Archivo no recibido")

    # 1) verificar médico
    med = await db.get(ListadoMedico, medico_id)
    if not med:
        raise HTTPException(404, "Médico no encontrado")

    # 2) guardar archivo físico
    try:
        saved = await _save_upload_for_medico(medico_id, up)
    except Exception as e:
        # si algo falla guardando en disco
        raise HTTPException(500, f"Error guardando el archivo: {e}")

    # 3) Persistir Documento
    try:
        doc = Documento(
            medico_id     = medico_id,
            label         = label,
            original_name = saved["original_name"],
            filename      = saved["safe_name"],            # si tenés esta columna
            content_type  = saved["content_type"],
            size          = saved["size"],
            path          = saved["rel_path"],             # <- relativo público
        )
        db.add(doc)
        await db.flush()   # asegura PK
    except SQLAlchemyError:
        # limpieza si falló DB
        try:
            saved["abs_path"].unlink(missing_ok=True)
        except Exception:
            pass
        await db.rollback()
        raise HTTPException(500, "Error registrando el documento en DB")

    # 4) Si mandás label y querés actualizar campos del modelo y/o conceps_espec (igual que tu admin)
    try:
        if label:
            field = LABEL_TO_FIELD.get(label.strip().lower())
            if field and hasattr(med, field):
                setattr(med, field, saved["rel_path"])  # guardo ruta pública
            await db.flush()

        # Opcional: si el label es resolucion/resolucion_n, actualizar conceps_espec
        # (mismo patrón que tu admin_upload_document)
        if label:
            lab = label.strip().lower()
            idx = None
            if lab == "resolucion":
                idx = 0
            else:
                m = re.match(r"resolucion_(\d+)$", lab)
                if m:
                    idx = int(m.group(1)) - 1  # 0..5

            if idx is not None:
                data = med.conceps_espec or {"conceps": [], "espec": []}
                espec = list(data.get("espec") or [])
                # asegura estructura
                while len(espec) <= idx:
                    espec.append({"id_colegio": None, "n_resolucion": None, "fecha_resolucion": None, "adjunto": None})
                espec[idx]["adjunto"] = str(doc.id)
                data["espec"] = espec
                med.conceps_espec = data
                flag_modified(med, "conceps_espec")
                await db.flush()

        await db.commit()
        await db.refresh(doc)
    except Exception:
        await db.rollback()
        # si algo falla acá, dejá el archivo y el doc creado (o borrá si querés)
        raise

    # 5) respuesta
    return {
        "id": int(doc.id),
        "file_name": doc.original_name,
        "url": "/" + str(doc.path).lstrip("/"),   # frontend ya lo usa así
        "pretty_label": label or "",
    }


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

    folder = MEDIA_URL / "medicos" / str(medico_id)
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

  
@router.post("/admin/register/{medico_id}/document",
             dependencies=[Depends(require_scope("medicos:agregar"))])
async def admin_upload_document(medico_id: int,
                                file: UploadFile = File(...),
                                label: Optional[str] = Form(None),
                                db: AsyncSession = Depends(get_db)):
    med = await db.get(ListadoMedico, medico_id)
    if not med:
        raise HTTPException(404, "Médico no encontrado")

    folder = MEDIA_URL / "medicos" / str(medico_id)
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

