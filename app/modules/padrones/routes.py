from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.database import get_db
from app.db.models import Especialidad, MedicoObraSocial, ListadoMedico, ObrasSociales
from app.modules.padrones.schemas import AsignacionesOut, ObraSocialOut, PadronOut, PadronUpdate, PageMedicoOS

router = APIRouter()


# region Helpers
def _os_number_col():
    return getattr(ObrasSociales, "NRO_OBRA_SOCIAL", getattr(ObrasSociales, "NRO_OBRASOCIAL"))


def _padron_number_attr():
    return getattr(MedicoObraSocial, "NRO_OBRASOCIAL")


async def _listado_defaults(db: AsyncSession, nro_socio: int):
    lm = (await db.execute(select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == nro_socio))).scalar_one_or_none()
    if not lm:
        return {}
    return {
        "NOMBRE": getattr(lm, "NOMBRE", None),
        "MATRICULA_PROV": getattr(lm, "MATRICULA_PROV", None),
        "MATRICULA_NAC": getattr(lm, "MATRICULA_NAC", None),
        "TELEFONO_CONSULTA": getattr(lm, "TELEFONO_CONSULTA", None),
    }
# endregion


# 1) Catálogo: listar obras sociales con MARCA = "S"
@router.get("/catalogo", response_model=List[ObraSocialOut])
async def catalogo_obras_sociales(
    marca: str = Query("S", description='Filtrar por MARCA; por defecto "S"'),
    db: AsyncSession = Depends(get_db),
):
    nro_col = _os_number_col()
    nombre_col = getattr(ObrasSociales, "OBRA_SOCIAL", None)
    if nombre_col is None:
        raise HTTPException(status_code=500, detail="No encuentro columna de nombre en ObrasSociales")

    stmt = select(nro_col.label("nro"), nombre_col.label("nombre")).where(ObrasSociales.MARCA == marca).order_by(nombre_col.asc())
    rows = (await db.execute(stmt)).all()

    out: list[ObraSocialOut] = []
    for nro, nombre in rows:
        codigo = None
        try:
            nint = int(nro)
            codigo = f"OS {nint:03d}"
        except Exception:
            pass
        out.append(ObraSocialOut(NRO_OBRA_SOCIAL=nro, NOMBRE=nombre, CODIGO=codigo))
    return out


# 2) Listar vínculos del médico
@router.get("/{nro_socio}", response_model=List[PadronOut])
async def list_padrones_de_medico(
    nro_socio: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(MedicoObraSocial)
        .join(ListadoMedico, MedicoObraSocial.NRO_SOCIO == ListadoMedico.NRO_SOCIO)
        .where(ListadoMedico.ID == nro_socio)
        .order_by(MedicoObraSocial.ID.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars())


@router.post("/{medico_id}/obras-sociales/{nro_os}", response_model=PadronOut)
async def create_padron_checkbox(
    response: Response,
    medico_id: int = Path(..., ge=1),
    nro_os: int = Path(..., ge=1),
    body: Optional[PadronUpdate] = Body(default=None),
    db: AsyncSession = Depends(get_db),
):
    medico = (
        await db.execute(select(ListadoMedico).where(ListadoMedico.ID == medico_id))
    ).scalar_one_or_none()
    if not medico:
        raise HTTPException(status_code=404, detail="Médico no encontrado")

    nro_socio = getattr(medico, "NRO_SOCIO", None)
    if not nro_socio:
        raise HTTPException(status_code=400, detail="Médico sin NRO_SOCIO")

    nro_col = _os_number_col()
    os_row = (
        await db.execute(
            select(ObrasSociales).where(
                and_(nro_col == nro_os, ObrasSociales.MARCA == "S")
            )
        )
    ).scalar_one_or_none()
    if not os_row:
        raise HTTPException(status_code=404, detail="Obra social no encontrada o inactiva")

    padron_os_attr = _padron_number_attr()
    existing = (
        await db.execute(
            select(MedicoObraSocial).where(
                and_(MedicoObraSocial.NRO_SOCIO == nro_socio, padron_os_attr == nro_os)
            )
        )
    ).scalar_one_or_none()

    defaults = await _listado_defaults(db, nro_socio)

    if existing:
        if body:
            for k, v in body.model_dump(exclude_unset=True).items():
                if hasattr(existing, k) and v is not None:
                    setattr(existing, k, v)
        for k, v in defaults.items():
            if getattr(existing, k, None) in (None, "", 0):
                setattr(existing, k, v)

        await db.commit()
        await db.refresh(existing)
        response.status_code = status.HTTP_200_OK
        return existing

    nuevo = MedicoObraSocial(
        NRO_SOCIO=nro_socio,
        **defaults,
        **({"NRO_OBRASOCIAL": nro_os}),
        **(body.model_dump(exclude_unset=True) if body else {}),
    )
    db.add(nuevo)
    await db.commit()
    await db.refresh(nuevo)
    response.status_code = status.HTTP_201_CREATED
    return nuevo


@router.delete("/{medico_id}/obras-sociales/{nro_os}", status_code=status.HTTP_200_OK)
async def delete_padron_checkbox(
    medico_id: int = Path(..., ge=1),
    nro_os: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    medico = (
        await db.execute(select(ListadoMedico).where(ListadoMedico.ID == medico_id))
    ).scalar_one_or_none()
    if not medico:
        return {"deleted": 0}

    nro_socio = getattr(medico, "NRO_SOCIO", None)
    if not nro_socio:
        return {"deleted": 0}

    padron_os_attr = _padron_number_attr()
    row = (
        await db.execute(
            select(MedicoObraSocial).where(
                and_(MedicoObraSocial.NRO_SOCIO == nro_socio, padron_os_attr == nro_os)
            )
        )
    ).scalar_one_or_none()

    if not row:
        return {"deleted": 0}

    await db.delete(row)
    await db.commit()
    return {"deleted": 1}


@router.get("/obras-sociales/{nro_os}/medicos", response_model=PageMedicoOS)
async def list_medicos_por_obra_social(
    nro_os: int = Path(..., ge=1),
    search: str | None = Query(None, description="Filtra por NOMBRE contiene o NRO_SOCIO exacto"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    padron_os_attr = _padron_number_attr()

    filters = [padron_os_attr == nro_os]

    if search:
        s = search.strip()
        if s.isdigit():
            filters.append(MedicoObraSocial.NRO_SOCIO == int(s))
        else:
            filters.append(MedicoObraSocial.NOMBRE.like(f"%{s}%"))

    LM = aliased(ListadoMedico)
    OS = aliased(ObrasSociales)

    esp_keys = ["ESP1", "ESP2", "ESP3", "ESP4", "ESP5", "ESP6"]

    stmt = (
        select(
            LM.ID.label("ID"),
            MedicoObraSocial.NRO_SOCIO.label("NRO_SOCIO"),
            MedicoObraSocial.NOMBRE.label("NOMBRE"),
            MedicoObraSocial.MATRICULA_PROV.label("MATRICULA_PROV"),
            MedicoObraSocial.MATRICULA_NAC.label("MATRICULA_NAC"),
            MedicoObraSocial.CATEGORIA.label("CATEGORIA"),
            MedicoObraSocial.TELEFONO_CONSULTA.label("TELEFONO_CONSULTA"),
            MedicoObraSocial.MARCA.label("MARCA"),
            OS.OBRA_SOCIAL.label("OBRA_SOCIAL"),
            LM.NRO_ESPECIALIDAD.label("ESP1"),
            LM.NRO_ESPECIALIDAD2.label("ESP2"),
            LM.NRO_ESPECIALIDAD3.label("ESP3"),
            LM.NRO_ESPECIALIDAD4.label("ESP4"),
            LM.NRO_ESPECIALIDAD5.label("ESP5"),
            LM.NRO_ESPECIALIDAD6.label("ESP6"),
        )
        .join(LM, LM.NRO_SOCIO == MedicoObraSocial.NRO_SOCIO)
        .outerjoin(OS, OS.NRO_OBRASOCIAL == padron_os_attr)
        .where(and_(*filters))
    )

    total_stmt = (
        select(func.count())
        .select_from(MedicoObraSocial)
        .join(LM, LM.NRO_SOCIO == MedicoObraSocial.NRO_SOCIO)
        .where(and_(*filters))
    )
    total = (await db.execute(total_stmt)).scalar_one()

    offset = (page - 1) * size
    result = await db.execute(
        stmt.order_by(MedicoObraSocial.NOMBRE.asc())
            .offset(offset)
            .limit(size)
    )
    rows = result.mappings().all()

    all_codes: set[int] = set()
    codes_per_row: list[list[int]] = []

    for r in rows:
        codes = []
        for k in esp_keys:
            v = r.get(k)
            if v is None:
                continue
            try:
                iv = int(v)
            except Exception:
                continue
            if iv > 0:
                codes.append(iv)
        codes = list(dict.fromkeys(codes))
        codes_per_row.append(codes)
        all_codes.update(codes)

    espec_map: dict[int, str] = {}
    if all_codes:
        espec_rows = await db.execute(
            select(Especialidad.ID_COLEGIO_ESPE, Especialidad.ESPECIALIDAD)
            .where(Especialidad.ID_COLEGIO_ESPE.in_(all_codes))
        )
        for code, name in espec_rows.all():
            if code is None:
                continue
            espec_map[int(code)] = (name or "").strip()

    items: list[dict] = []
    for r, codes in zip(rows, codes_per_row):
        especialidades = [espec_map.get(c) for c in codes if espec_map.get(c)]

        items.append({
            "ID": int(r["ID"]),
            "NRO_SOCIO": int(r["NRO_SOCIO"]),
            "NOMBRE": (r["NOMBRE"] or "").strip(),
            "MATRICULA_PROV": r["MATRICULA_PROV"],
            "MATRICULA_NAC": r["MATRICULA_NAC"],
            "CATEGORIA": r["CATEGORIA"],
            "TELEFONO_CONSULTA": r["TELEFONO_CONSULTA"],
            "MARCA": r["MARCA"],
            "OBRA_SOCIAL": (r["OBRA_SOCIAL"] or "").strip() if r["OBRA_SOCIAL"] is not None else None,
            "ESPECIALIDADES": especialidades,
        })

    return {"items": items, "total": total, "page": page, "size": size}


# region Asignaciones
def _ensure_json(doc: dict | None) -> dict:
    if not isinstance(doc, dict):
        return {"conceps": [], "espec": []}
    conceps = list(map(int, (doc.get("conceps") or [])))
    espec_raw = doc.get("espec") or []
    espec_ids: list[int] = []
    if espec_raw and isinstance(espec_raw, list):
        if espec_raw and isinstance(espec_raw[0], dict):
            for it in espec_raw:
                try:
                    espec_ids.append(int(it.get("id_colegio") or it.get("id_colegio_espe") or 0))
                except Exception:
                    pass
        else:
            espec_ids = [int(x) for x in espec_raw]
    return {"conceps": conceps, "espec": espec_ids}


@router.get("/{medico_id}/asignaciones", response_model=AsignacionesOut)
async def get_asignaciones(medico_id: int, db: AsyncSession = Depends(get_db)):
    med = await db.get(ListadoMedico, medico_id)
    if not med:
        raise HTTPException(404, "Médico no encontrado")
    data = _ensure_json(med.conceps_espec)
    return AsignacionesOut(**data)


@router.post("/{medico_id}/asignaciones/concepto", response_model=AsignacionesOut)
async def add_concepto(medico_id: int, nro_concepto: int, db: AsyncSession = Depends(get_db)):
    med = await db.get(ListadoMedico, medico_id)
    if not med:
        raise HTTPException(404, "Médico no encontrado")
    data = _ensure_json(med.conceps_espec)
    if nro_concepto not in data["conceps"]:
        full = med.conceps_espec or {"conceps": [], "espec": []}
        full["conceps"] = data["conceps"]
        med.conceps_espec = full
        await db.flush()
        await db.commit()
    return AsignacionesOut(**data)


@router.delete("/{medico_id}/asignaciones/concepto/{nro_concepto}", response_model=AsignacionesOut)
async def remove_concepto(medico_id: int, nro_concepto: int, db: AsyncSession = Depends(get_db)):
    med = await db.get(ListadoMedico, medico_id)
    if not med:
        raise HTTPException(404, "Médico no encontrado")
    data = _ensure_json(med.conceps_espec)
    full = med.conceps_espec or {"conceps": [], "espec": []}
    full["conceps"] = data["conceps"]
    med.conceps_espec = full
    await db.flush()
    await db.commit()
    return AsignacionesOut(**data)


@router.post("/{medico_id}/asignaciones/especialidad", response_model=AsignacionesOut)
async def add_especialidad(medico_id: int, esp_id: int, db: AsyncSession = Depends(get_db)):
    med = await db.get(ListadoMedico, medico_id)
    if not med:
        raise HTTPException(404, "Médico no encontrado")
    data = _ensure_json(med.conceps_espec)
    if esp_id not in data["espec"]:
        data["espec"].append(int(esp_id))
        med.conceps_espec = data
        await db.flush()
        await db.commit()
    return AsignacionesOut(**data)


@router.delete("/{medico_id}/asignaciones/especialidad/{esp_id}", response_model=AsignacionesOut)
async def remove_especialidad(medico_id: int, esp_id: int, db: AsyncSession = Depends(get_db)):
    med = await db.get(ListadoMedico, medico_id)
    if not med:
        raise HTTPException(404, "Médico no encontrado")
    data = _ensure_json(med.conceps_espec)
    data["espec"] = [e for e in data["espec"] if int(e) != int(esp_id)]
    med.conceps_espec = data
    await db.flush()
    await db.commit()
    return AsignacionesOut(**data)
# endregion
