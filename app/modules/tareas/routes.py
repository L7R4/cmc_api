"""Cuaderno de tareas personal del Inicio del panel.

Cada usuario ve y modifica SOLO las suyas. El dueño sale del token (`uid`), nunca
del path ni del body; ningún parámetro permite elegir otro usuario. Una tarea
ajena responde 404, igual que una inexistente.

No hay scope que aplique (cualquier usuario autenticado tiene su cuaderno): la
restricción es de propiedad. Ver SOLO_AUTENTICADO en app/auth/authz.py.
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.database import get_db
from app.modules.tareas import service
from app.modules.tareas.schemas import (
    ImportarIn,
    TareaCreate,
    TareaOut,
    TareaUpdate,
)

router = APIRouter()  # La autorización la declara app/auth/authz.py::SCOPES_POR_RUTA.


def _dueno(user: dict) -> int:
    uid = user.get("uid")
    if uid is None:
        # Token viejo sin el claim: el front renueva con /auth/refresh y sigue.
        raise HTTPException(401, "token_sin_identidad")
    return int(uid)


@router.get("/", response_model=List[TareaOut])
async def listar_tareas(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await service.listar(db, _dueno(user))


@router.post("/", response_model=TareaOut, status_code=status.HTTP_201_CREATED)
async def crear_tarea(
    payload: TareaCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    obj = await service.crear(db, _dueno(user), payload.texto, payload.prioridad)
    await db.commit()
    return obj


# Las rutas fijas van antes de `/{tarea_id}`: si no, "hechas" se intenta leer como id.
@router.post("/importar", response_model=List[TareaOut])
async def importar_tareas(
    payload: ImportarIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Vuelca las tareas que el usuario tenía guardadas en su navegador."""
    filas = await service.importar(db, _dueno(user), payload.tareas)
    await db.commit()
    return filas


@router.delete("/hechas", status_code=status.HTTP_204_NO_CONTENT)
async def limpiar_hechas(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    await service.limpiar_hechas(db, _dueno(user))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{tarea_id}", response_model=TareaOut)
async def actualizar_tarea(
    payload: TareaUpdate,
    tarea_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    obj = await service.actualizar(
        db,
        _dueno(user),
        tarea_id,
        texto=payload.texto,
        hecha=payload.hecha,
        prioridad=payload.prioridad,
    )
    await db.commit()
    return obj


@router.delete("/{tarea_id}", status_code=status.HTTP_204_NO_CONTENT)
async def quitar_tarea(
    tarea_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    await service.quitar(db, _dueno(user), tarea_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
