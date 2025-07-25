from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.crud import compute_liquidacion, get_medico
from app.api.deps import get_async_db

router = APIRouter()

@router.get("/{medico_id}", response_model=Any)
async def get_liquidacion(
    medico_id: int,
    periodo_id: int = Query(..., description="ID del periodo"),
    db: AsyncSession = Depends(get_async_db),
):
    # Verifico que exista el médico
    medico = await get_medico(db, medico_id)
    if not medico:
        raise HTTPException(status_code=404, detail="Médico no encontrado")

    return await compute_liquidacion(db, medico_id, periodo_id)
