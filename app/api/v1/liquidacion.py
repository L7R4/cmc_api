from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.services.liquidaciones import generar_preview

router = APIRouter()

class GenerarReq(BaseModel):
    obra_sociales_solicitadas: List[int] = Field(..., default_factory=list)
    periodos_solicitados: List[str] = Field(..., default_factory=list)

@router.post("/generar")
async def generar(req: GenerarReq, db: AsyncSession = Depends(get_db)):
    out = await generar_preview(db, req.obra_sociales_solicitadas, req.periodos_solicitados)
    if out.get("status") != "ok":
        raise HTTPException(400, out.get("message", "error"))
    return out  # EXACTO al formato requerido
