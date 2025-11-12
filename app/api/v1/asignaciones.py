# app/api/routers/medico_asignaciones.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.db.models import ListadoMedico
from app.schemas.descuentos_especialidades_schemas import AsignacionesOut

router = APIRouter()

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
        await db.flush(); await db.commit()
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
    await db.flush(); await db.commit()
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
        await db.flush(); await db.commit()
    return AsignacionesOut(**data)

@router.delete("/{medico_id}/asignaciones/especialidad/{esp_id}", response_model=AsignacionesOut)
async def remove_especialidad(medico_id: int, esp_id: int, db: AsyncSession = Depends(get_db)):
    med = await db.get(ListadoMedico, medico_id)
    if not med:
        raise HTTPException(404, "Médico no encontrado")
    data = _ensure_json(med.conceps_espec)
    data["espec"] = [e for e in data["espec"] if int(e) != int(esp_id)]
    med.conceps_espec = data
    await db.flush(); await db.commit()
    return AsignacionesOut(**data)
