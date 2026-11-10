"""Importar valores fijos NE desde Excel: vista previa + aplicar (ver `importar_fijos`)."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.database import get_db
from app.modules.nomenclador import importar_fijos
from app.modules.nomenclador.schemas import (
    ImportarFijosAplicarIn,
    ImportarFijosAplicarOut,
    ImportarFijosIn,
    ImportarFijosPreviewOut,
    ReplicaOSOut,
)

router = APIRouter()


@router.post("/importar_fijos/previsualizar", response_model=ImportarFijosPreviewOut)
async def previsualizar_valores_fijos(body: ImportarFijosIn, db: AsyncSession = Depends(get_db)):
    """Qué pasaría con cada fila del Excel y qué se puede hacer con ella. No escribe."""
    return await importar_fijos.previsualizar(db, body)


@router.post("/importar_fijos/aplicar", response_model=ImportarFijosAplicarOut)
async def aplicar_valores_fijos(
    body: ImportarFijosAplicarIn,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aplica las decisiones de la vista previa. Todo o nada: si una fila falla, no se
    carga ninguna. 409 si los datos cambiaron desde la vista previa."""
    try:
        out = await importar_fijos.aplicar(db, body, str(user.get("nro_socio", "")) or None)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return out


@router.post("/importar_fijos/replicar", response_model=list[ReplicaOSOut])
async def replicar_valores_fijos(
    body: ImportarFijosAplicarIn,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Carga en las O.S. de `replicar_en` lo ya cargado en la principal. Pensado para ir de a una O.S.
    después de `aplicar` con `replicar_en` vacío: pedidos cortos y con avance. Cada O.S. es independiente:
    si una falla queda sin cambios (viene en `estado: error`) y se puede reintentar sola."""
    try:
        out = await importar_fijos.replicar(db, body, str(user.get("nro_socio", "")) or None)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return out
