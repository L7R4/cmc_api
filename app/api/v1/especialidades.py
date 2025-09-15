# app/api/routers/especialidades.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.db.database import get_db
from app.db.models import Especialidad
from app.schemas.descuentos_especialidades_schemas import EspecialidadOut

router = APIRouter()

@router.get("/", response_model=List[EspecialidadOut])
async def list_especialidades(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Especialidad))).scalars().all()
    return [{"id": int(r.ID), "id_colegio_espe": int(r.ID_COLEGIO_ESPE),"nombre": str(r.ESPECIALIDAD)} for r in rows]
