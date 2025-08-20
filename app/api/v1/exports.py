
from fastapi import APIRouter, HTTPException
from io import BytesIO
from fastapi.responses import StreamingResponse
from typing import Any, Dict, List, Optional
from fastapi import Body
from datetime import datetime
from app.services.exports import build_excel_from_liquidacion

router = APIRouter()

@router.post("/exportar_excel_for_liquidacion", summary="Exportar Excel desde Liquidación",
    description="Recibe un JSON de liquidación y devuelve un archivo Excel con Resumen")
async def exportar_excel_desde_json(
    data: Dict[str, Any] = Body(..., description="JSON de liquidación")
):
    """
    Recibe el JSON de la liquidación y devuelve un archivo Excel con:
    - 'Resumen'
    - 'Detalle por médico'
    - 'Prestaciones'
    """
    try:
        content = build_excel_from_liquidacion(data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"No se pudo generar el Excel: {e}")

    filename = f"liquidacion_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )