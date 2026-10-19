"""Etapa 3 del nomenclador: "Códigos por obra social" — dar de alta un código en
una O.S. (sin precio), editar sus datos y quién lo factura, suspenderlo.

Ver `alta_os.py` para el flujo completo de 4 etapas.
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.database import get_db
from app.modules.nomenclador import alta_os, service
from app.modules.nomenclador.schemas import (
    AltaCodigosIn,
    AltaCodigosOut,
    CodigoObraSocialOut,
    CodigoObraSocialUpdate,
    CodigosPorOSOut,
    EstadoCodigoOS,
)

router = APIRouter()


@router.get("/", response_model=CodigosPorOSOut)
async def listar_codigos_por_os(
    obra_social_nro: int = Query(...),
    estado: Optional[EstadoCodigoOS] = Query(None),
    q: Optional[str] = Query(None),
    tipo: Optional[Literal[service.TIPOS_FILTRO]] = Query(
        None, description="Consulta / Practica / Honorarios individuales, por la categoría del código en la O.S.",
    ),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Todos los códigos activos del catálogo con su estado en la O.S.: sin alta /
    sin precio / con precio / suspendido, más los conteos por estado."""
    return await alta_os.listar_por_os(
        db, obra_social_nro, estado=estado, q=q, tipo=tipo, page=page, size=size,
    )


@router.post("/alta", response_model=AltaCodigosOut)
async def dar_de_alta(
    body: AltaCodigosIn,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Da de alta (sin precio) uno o varios códigos en una o varias O.S. Lo no
    informado se toma del catálogo: descripción y quién factura (plantilla). Éxito
    parcial: cada (O.S., código) se informa por separado."""
    resultados = await alta_os.dar_de_alta(db, body, str(user.get("nro_socio", "")) or None)
    await db.commit()
    return AltaCodigosOut(resultados=resultados)


@router.get("/{obra_social_nro}/{nomenclador_id}", response_model=CodigoObraSocialOut)
async def obtener_codigo_os(obra_social_nro: int, nomenclador_id: int, db: AsyncSession = Depends(get_db)):
    return await alta_os.detalle_par(db, obra_social_nro, nomenclador_id)


@router.patch("/{obra_social_nro}/{nomenclador_id}", response_model=CodigoObraSocialOut)
async def actualizar_codigo_os(
    obra_social_nro: int, nomenclador_id: int, body: CodigoObraSocialUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Datos del código en la O.S. y quién lo factura. Se copian también a sus
    variantes de precio activas."""
    out = await alta_os.actualizar_par(db, obra_social_nro, nomenclador_id, body)
    await db.commit()
    return out


@router.post("/{obra_social_nro}/{nomenclador_id}/suspender", response_model=CodigoObraSocialOut)
async def suspender_codigo_os(obra_social_nro: int, nomenclador_id: int, db: AsyncSession = Depends(get_db)):
    """La O.S. deja de reconocer el código: facturación lo rechaza. Los precios y el
    historial quedan guardados."""
    out = await alta_os.cambiar_estado(db, obra_social_nro, nomenclador_id, "suspendido")
    await db.commit()
    return out


@router.post("/{obra_social_nro}/{nomenclador_id}/reactivar", response_model=CodigoObraSocialOut)
async def reactivar_codigo_os(obra_social_nro: int, nomenclador_id: int, db: AsyncSession = Depends(get_db)):
    out = await alta_os.cambiar_estado(db, obra_social_nro, nomenclador_id, "activo")
    await db.commit()
    return out
