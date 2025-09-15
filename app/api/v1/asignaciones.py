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
    return {
        "conceps": list(map(int, doc.get("conceps", []) or [])),
        "espec": list(map(int, doc.get("espec", []) or [])),
    }

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
        data["conceps"].append(int(nro_concepto))
        med.conceps_espec = data
        await db.flush(); await db.commit()
    return AsignacionesOut(**data)

@router.delete("/{medico_id}/asignaciones/concepto/{nro_concepto}", response_model=AsignacionesOut)
async def remove_concepto(medico_id: int, nro_concepto: int, db: AsyncSession = Depends(get_db)):
    med = await db.get(ListadoMedico, medico_id)
    if not med:
        raise HTTPException(404, "Médico no encontrado")
    data = _ensure_json(med.conceps_espec)
    data["conceps"] = [c for c in data["conceps"] if int(c) != int(nro_concepto)]
    med.conceps_espec = data
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
