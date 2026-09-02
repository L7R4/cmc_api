from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.auth.deps import get_current_user
from app.auth.ownership import medico_objetivo
from app.db.database import get_db
from app.db.models import Especialidad, MedicoObraSocial, ListadoMedico, ObrasSociales
from app.modules.catalogs.familia_padron import codigos_de_familia
from app.modules.padrones.schemas import (
    AsignacionesOut,
    MedicoOSItemOut,
    ObraSocialOut,
    PadronOut,
    PadronUpdate,
    PageMedicoOS,
)

import logging

log = logging.getLogger(__name__)

router = APIRouter()


# region Helpers
def _os_number_col():
    return getattr(ObrasSociales, "NRO_OBRA_SOCIAL", getattr(ObrasSociales, "NRO_OBRASOCIAL"))


def _padron_number_attr():
    return getattr(MedicoObraSocial, "NRO_OBRASOCIAL")


def _clean_str(v) -> str | None:
    """Coacciona un valor de la fila a str limpio (o None).

    Algunas columnas de `listado_medico` (p. ej. CODIGO_POSTAL, CUIT) llegan
    como int desde MySQL aunque el modelo las declare String, así que no se
    puede asumir .strip(). str() cubre int/str/None de forma segura.
    """
    if v is None:
        return None
    s = str(v).strip()
    return s or None


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
#
# Sólo cabezas de familia (`obra_social_principal_id IS NULL`): una empresa
# con varios planes (Swiss Medical, Medife, Sancor...) tiene que aparecer
# UNA sola vez acá. Las asociadas siguen existiendo en `obras_sociales` para
# facturación/valores, sólo se ocultan de este selector de padrón.
@router.get("/catalogo", response_model=List[ObraSocialOut])
async def catalogo_obras_sociales(
    marca: str = Query("S", description='Filtrar por MARCA; por defecto "S"'),
    db: AsyncSession = Depends(get_db),
):
    nro_col = _os_number_col()
    nombre_col = getattr(ObrasSociales, "OBRA_SOCIAL", None)
    if nombre_col is None:
        raise HTTPException(status_code=500, detail="No encuentro columna de nombre en ObrasSociales")

    stmt = (
        select(nro_col.label("nro"), nombre_col.label("nombre"))
        .where(ObrasSociales.MARCA == marca, ObrasSociales.obra_social_principal_id.is_(None))
        .order_by(nombre_col.asc())
    )
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
    user: dict = Depends(get_current_user),
):
    """Obras sociales en las que el médico está empadronado.

    OJO con el nombre del parámetro: se filtra por `ListadoMedico.ID`, no por
    `NRO_SOCIO`. Por eso el helper es `medico_objetivo` (compara contra el claim
    `uid`) y no `socio_objetivo` — ver la explicación de los dos identificadores
    en app/auth/ownership.py.
    """
    nro_socio = medico_objetivo(user, nro_socio)
    stmt = (
        select(MedicoObraSocial)
        .join(ListadoMedico, MedicoObraSocial.NRO_SOCIO == ListadoMedico.NRO_SOCIO)
        .where(ListadoMedico.ID == nro_socio)
        .order_by(MedicoObraSocial.ID.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars())


async def _upsert_una_fila(
    db: AsyncSession,
    nro_socio: int,
    nro_os: int,
    body: Optional[PadronUpdate],
    defaults: dict,
) -> tuple[Optional[MedicoObraSocial], bool]:
    """Upsert de una sola fila `(nro_socio, nro_os)`.

    Devuelve `(fila, creada)`. `fila=None` si `nro_os` no tiene catálogo
    `MARCA='S'` activo — el llamador decide si eso es un error (código
    pedido explícitamente) o se omite (otro miembro de la familia).
    """
    nro_col = _os_number_col()
    os_row = (
        await db.execute(
            select(ObrasSociales).where(and_(nro_col == nro_os, ObrasSociales.MARCA == "S"))
        )
    ).scalar_one_or_none()
    if not os_row:
        return None, False

    padron_os_attr = _padron_number_attr()
    existing = (
        await db.execute(
            select(MedicoObraSocial).where(
                and_(MedicoObraSocial.NRO_SOCIO == nro_socio, padron_os_attr == nro_os)
            )
        )
    ).scalar_one_or_none()

    if existing:
        if body:
            for k, v in body.model_dump(exclude_unset=True).items():
                if hasattr(existing, k) and v is not None:
                    setattr(existing, k, v)
        for k, v in defaults.items():
            if getattr(existing, k, None) in (None, "", 0):
                setattr(existing, k, v)
        return existing, False

    nuevo = MedicoObraSocial(
        NRO_SOCIO=nro_socio,
        **defaults,
        **({"NRO_OBRASOCIAL": nro_os}),
        **(body.model_dump(exclude_unset=True) if body else {}),
    )
    db.add(nuevo)
    return nuevo, True


@router.post("/{medico_id}/obras-sociales/{nro_os}", response_model=PadronOut)
async def create_padron_checkbox(
    response: Response,
    medico_id: int = Path(..., ge=1),
    nro_os: int = Path(..., ge=1),
    body: Optional[PadronUpdate] = Body(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Tilda el padrón de `nro_os` para el médico — y el de TODA su familia.

    Una empresa con varios planes (Swiss Medical, Medife, Sancor...) es un
    único padrón: tildar "Swiss Medical" tiene que dar de alta al médico en
    los 4 códigos, no sólo en el que aparece en el selector. Ver
    `app/modules/catalogs/familia_padron.py`.
    """
    medico = (
        await db.execute(select(ListadoMedico).where(ListadoMedico.ID == medico_id))
    ).scalar_one_or_none()
    if not medico:
        raise HTTPException(status_code=404, detail="Médico no encontrado")

    nro_socio = getattr(medico, "NRO_SOCIO", None)
    if not nro_socio:
        raise HTTPException(status_code=400, detail="Médico sin NRO_SOCIO")

    familia = await codigos_de_familia(db, nro_os)
    defaults = await _listado_defaults(db, nro_socio)

    filas_por_codigo: dict[int, MedicoObraSocial] = {}
    creada_solicitada = False
    omitidos: list[int] = []
    for miembro in familia:
        fila, creada = await _upsert_una_fila(db, nro_socio, miembro, body, defaults)
        if fila is None:
            omitidos.append(miembro)
            continue
        filas_por_codigo[miembro] = fila
        if miembro == nro_os:
            creada_solicitada = creada

    fila_solicitada = filas_por_codigo.get(nro_os)
    if fila_solicitada is None:
        raise HTTPException(status_code=404, detail="Obra social no encontrada o inactiva")

    if omitidos:
        log.warning(
            "padron: familia de %s tiene miembros sin catálogo activo, se omiten: %s",
            nro_os, omitidos,
        )

    await db.commit()
    await db.refresh(fila_solicitada)
    response.status_code = status.HTTP_201_CREATED if creada_solicitada else status.HTTP_200_OK
    return fila_solicitada


@router.delete("/{medico_id}/obras-sociales/{nro_os}", status_code=status.HTTP_200_OK)
async def delete_padron_checkbox(
    medico_id: int = Path(..., ge=1),
    nro_os: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Destilda el padrón de `nro_os` para el médico — y el de toda su
    familia (ver `create_padron_checkbox`)."""
    medico = (
        await db.execute(select(ListadoMedico).where(ListadoMedico.ID == medico_id))
    ).scalar_one_or_none()
    if not medico:
        return {"deleted": 0}

    nro_socio = getattr(medico, "NRO_SOCIO", None)
    if not nro_socio:
        return {"deleted": 0}

    familia = await codigos_de_familia(db, nro_os)
    padron_os_attr = _padron_number_attr()
    filas = (
        await db.execute(
            select(MedicoObraSocial).where(
                and_(MedicoObraSocial.NRO_SOCIO == nro_socio, padron_os_attr.in_(familia))
            )
        )
    ).scalars().all()

    for fila in filas:
        await db.delete(fila)
    await db.commit()
    return {"deleted": len(filas)}


async def _armar_query_medicos_por_os(db: AsyncSession, nro_os: int, search: str | None):
    """Arma el SELECT del padrón de una obra social — expandido a toda su
    familia — sin `ORDER BY`/`OFFSET`/`LIMIT`. Compartido por el endpoint
    paginado y por el de exportación: ambos tienen que devolver exactamente
    el mismo universo de filas, sólo cambia cuánto de ese universo se manda.

    Una empresa con varios planes (Swiss Medical, Medife, Sancor...) manda
    UN padrón; pedir cualquiera de sus códigos devuelve el mismo resultado.
    Sin la dedup por `NRO_SOCIO` el `IN(...)` de la familia multiplicaría
    filas (un prestador empadronado en 3 planes saldría 3 veces), y encima
    la tabla legacy tiene pares `(NRO_SOCIO, NRO_OBRASOCIAL)` duplicados —
    de ahí el `MIN(ID)` como desempate determinístico.
    """
    padron_os_attr = _padron_number_attr()
    familia = await codigos_de_familia(db, nro_os)

    LM = aliased(ListadoMedico)

    search_filters = []
    if search:
        s = search.strip()
        if s.isdigit():
            search_filters.append(LM.NRO_SOCIO == int(s))
        else:
            search_filters.append(LM.NOMBRE.like(f"%{s}%"))

    # Una fila elegida por NRO_SOCIO entre todos los códigos de la familia
    # (y sus duplicados internos): el MIN(ID) es determinístico e idempotente.
    chosen_id_subq = (
        select(func.min(MedicoObraSocial.ID))
        .where(padron_os_attr.in_(familia))
        .group_by(MedicoObraSocial.NRO_SOCIO)
        .scalar_subquery()
    )

    # `listado_medico` también tiene NRO_SOCIO repetidos (datos legacy, no hay
    # unique constraint) — sin este segundo dedup, esos socios saldrían
    # duplicados en el listado aunque `chosen_id_subq` ya haya elegido una
    # única fila de `medico_obra_social` para ellos.
    lm_min_id_subq = (
        select(ListadoMedico.NRO_SOCIO, func.min(ListadoMedico.ID).label("min_id"))
        .group_by(ListadoMedico.NRO_SOCIO)
        .subquery()
    )

    esp_keys = ["ESP1", "ESP2", "ESP3", "ESP4", "ESP5", "ESP6"]

    stmt = (
        select(
            LM.ID.label("ID"),
            LM.NRO_SOCIO.label("NRO_SOCIO"),
            LM.NOMBRE.label("NOMBRE"),
            LM.MATRICULA_PROV.label("MATRICULA_PROV"),
            LM.MATRICULA_NAC.label("MATRICULA_NAC"),
            LM.CATEGORIA.label("CATEGORIA"),
            LM.TELEFONO_CONSULTA.label("TELEFONO_CONSULTA"),
            LM.DOMICILIO_CONSULTA.label("DOMICILIO_CONSULTA"),
            LM.MAIL_PARTICULAR.label("MAIL_PARTICULAR"),
            LM.CUIT.label("CUIT"),
            LM.CODIGO_POSTAL.label("CODIGO_POSTAL"),
            MedicoObraSocial.MARCA.label("MARCA"),
            LM.NRO_ESPECIALIDAD.label("ESP1"),
            LM.NRO_ESPECIALIDAD2.label("ESP2"),
            LM.NRO_ESPECIALIDAD3.label("ESP3"),
            LM.NRO_ESPECIALIDAD4.label("ESP4"),
            LM.NRO_ESPECIALIDAD5.label("ESP5"),
            LM.NRO_ESPECIALIDAD6.label("ESP6"),
        )
        .join(lm_min_id_subq, lm_min_id_subq.c.NRO_SOCIO == MedicoObraSocial.NRO_SOCIO)
        .join(LM, LM.ID == lm_min_id_subq.c.min_id)
        .where(and_(MedicoObraSocial.ID.in_(chosen_id_subq), *search_filters))
    )

    total_stmt = (
        select(func.count(func.distinct(MedicoObraSocial.NRO_SOCIO)))
        .select_from(MedicoObraSocial)
        .join(LM, LM.NRO_SOCIO == MedicoObraSocial.NRO_SOCIO)
        .where(and_(padron_os_attr.in_(familia), *search_filters))
    )
    total = (await db.execute(total_stmt)).scalar_one()

    # Nombre único de la familia (el de la cabeza), no el de la fila que
    # matcheó — antes variaba según qué código específico traía cada socio.
    obra_social_nombre = (
        await db.execute(
            select(ObrasSociales.OBRA_SOCIAL).where(
                ObrasSociales.NRO_OBRASOCIAL.in_(familia),
                ObrasSociales.obra_social_principal_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if obra_social_nombre is None:
        obra_social_nombre = (
            await db.execute(
                select(ObrasSociales.OBRA_SOCIAL).where(ObrasSociales.NRO_OBRASOCIAL == nro_os)
            )
        ).scalar_one_or_none()

    return stmt, LM, esp_keys, total, obra_social_nombre


async def _filas_a_items(
    db: AsyncSession,
    rows,
    esp_keys: list[str],
    obra_social_nombre: str | None,
) -> list[dict]:
    """Enriquece cada fila con nombres de especialidad y arma el dict que
    espera `MedicoOSItemOut`. Compartido por el listado paginado y el
    export: ambos parten del mismo `stmt` de `_armar_query_medicos_por_os`.
    """
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
            "DOMICILIO_CONSULTA": _clean_str(r["DOMICILIO_CONSULTA"]),
            "MAIL_PARTICULAR": _clean_str(r["MAIL_PARTICULAR"]),
            "CUIT": _clean_str(r["CUIT"]),
            "CODIGO_POSTAL": _clean_str(r["CODIGO_POSTAL"]),
            "MARCA": r["MARCA"],
            "OBRA_SOCIAL": (obra_social_nombre or "").strip() if obra_social_nombre is not None else None,
            "ESPECIALIDADES": especialidades,
        })

    return items


@router.get("/obras-sociales/{nro_os}/medicos", response_model=PageMedicoOS)
async def list_medicos_por_obra_social(
    nro_os: int = Path(..., ge=1),
    search: str | None = Query(None, description="Filtra por NOMBRE contiene o NRO_SOCIO exacto"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Padrón de una obra social — expandido a toda su familia, paginado.

    Pensado para la grilla en pantalla. Para exportar el padrón completo
    (Excel/PDF) usar `GET .../medicos/export`, que devuelve el mismo
    universo de filas sin paginar.
    """
    stmt, LM, esp_keys, total, obra_social_nombre = await _armar_query_medicos_por_os(db, nro_os, search)

    offset = (page - 1) * size
    result = await db.execute(
        stmt.order_by(LM.NOMBRE.asc())
            .offset(offset)
            .limit(size)
    )
    rows = result.mappings().all()

    items = await _filas_a_items(db, rows, esp_keys, obra_social_nombre)

    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/obras-sociales/{nro_os}/medicos/export", response_model=List[MedicoOSItemOut])
async def list_medicos_por_obra_social_export(
    nro_os: int = Path(..., ge=1),
    search: str | None = Query(None, description="Mismo filtro que el listado paginado"),
    db: AsyncSession = Depends(get_db),
):
    """Igual que `GET .../medicos`, pero sin paginar: pensado para que el
    front arme la exportación (Excel/PDF) con una sola consulta en vez de
    recorrer 5-6 páginas de `size=200`. Mismos filtros, misma familia,
    mismo dedup — sólo cambia que acá no hay `page`/`size`/`total`, es la
    lista completa directamente.
    """
    stmt, LM, esp_keys, _total, obra_social_nombre = await _armar_query_medicos_por_os(db, nro_os, search)

    result = await db.execute(stmt.order_by(LM.NOMBRE.asc()))
    rows = result.mappings().all()

    return await _filas_a_items(db, rows, esp_keys, obra_social_nombre)


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
async def get_asignaciones(
    medico_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    medico_id = medico_objetivo(user, medico_id)
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
