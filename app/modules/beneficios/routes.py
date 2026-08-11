"""ABM de beneficios / convenios para socios (web admin).

El ABM entero exige el scope `beneficios:gestionar`. Aparte va `router_socio`,
de sólo lectura, que es lo que consume el portal del socio (Inicio médico): un
médico no tiene ese scope y no debe verse obligado a pedirlo para mirar la
vidriera de convenios. La app móvil tiene su propia lectura en el BFF
(GET /api/mobile/beneficios).
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_scope
from app.db.database import get_db
from app.db.models.beneficios import CATEGORIAS_BENEFICIO, Beneficio
from app.modules.beneficios import service
from app.modules.beneficios.schemas import (
    BeneficioCreate,
    BeneficioOut,
    BeneficioUpdate,
)

router = APIRouter()  # El scope lo declara app/auth/authz.py::SCOPES_POR_RUTA (fuente unica de autorizacion).

# Sólo autenticación: cualquier socio logueado puede leer los convenios vigentes.
router_socio = APIRouter()

MAX_PAGE_SIZE = 200

# Tope de la vidriera del portal. El Inicio médico muestra un puñado, no el
# catálogo entero — para eso irá su propia pantalla más adelante.
MAX_VIGENTES = 24


@router_socio.get("/vigentes", response_model=List[BeneficioOut])
async def listar_vigentes(
    limit: int = Query(MAX_VIGENTES, ge=1, le=MAX_VIGENTES),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """Convenios activos y no vencidos, para el portal del socio.

    Sin scope de administración a propósito (ver docstring del módulo). No
    pagina: el tope es `MAX_VIGENTES` y la vidriera muestra menos todavía.
    """
    return await service.listar_vigentes(db, limit=limit)


@router.get("/categorias", response_model=List[str])
async def listar_categorias():
    """Catálogo fijo de categorías — pobla el select del formulario."""
    return list(CATEGORIAS_BENEFICIO)


@router.get("/", response_model=List[BeneficioOut])
async def listar_beneficios(
    response: Response,
    categoria: Optional[str] = Query(None, description="Filtrar por categoría (exacto)"),
    activo: Optional[bool] = Query(None, description="Filtrar por estado"),
    q: Optional[str] = Query(None, max_length=120, description="Busca en título y ubicación"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=MAX_PAGE_SIZE),
    db: AsyncSession = Depends(get_db),
):
    """Listado paginado. El total va en el header X-Total-Count (mismo criterio
    que el resto del panel, ver getJSONWithHeaders del front)."""
    total = await service.contar(db, categoria=categoria, activo=activo, q=q)
    response.headers["X-Total-Count"] = str(total)
    return await service.listar(
        db, categoria=categoria, activo=activo, q=q, skip=skip, limit=limit
    )


@router.get("/{beneficio_id}", response_model=BeneficioOut)
async def obtener_beneficio(
    beneficio_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    return await service.obtener_o_404(db, beneficio_id)


@router.post("/", response_model=BeneficioOut, status_code=status.HTTP_201_CREATED)
async def crear_beneficio(
    payload: BeneficioCreate,
    db: AsyncSession = Depends(get_db),
):
    obj = Beneficio(**payload.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.patch("/{beneficio_id}", response_model=BeneficioOut)
async def actualizar_beneficio(
    payload: BeneficioUpdate,
    beneficio_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    obj = await service.obtener_o_404(db, beneficio_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/{beneficio_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_beneficio(
    beneficio_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> Response:
    obj = await service.obtener_o_404(db, beneficio_id)
    await db.delete(obj)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
