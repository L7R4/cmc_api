from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Any, List, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.services.liquidaciones import generar_preview

router = APIRouter()

class GenerarReq(BaseModel):
    # JSON: {"obra_sociales_con_periodos": {"300":["2025-04","2025-07"], "412":["2025-06","2025-07"]}}
    obra_sociales_con_periodos: Dict[int, List[str]] = Field(
        ...,
        description="Mapa de obra social -> lista de periodos 'YYYY-MM'"
    )
@router.post("/generar")
async def generar(req: GenerarReq, db: AsyncSession = Depends(get_db)) -> Any:
    salida = await generar_preview(db, req.obra_sociales_con_periodos)
    if salida.get("status") != "ok":
        raise HTTPException(400, salida.get("message", "error"))
    return salida