"""Nomencladores nivelados (Cirugía adulto 7/10, Cirugía infantil, FASGO, Urología…):
consulta y edición de código → nivel, y aplicarlos a una obra social. Ver
`nivelados.py`."""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.database import get_db
from app.modules.nomenclador import nivelados
from app.modules.nomenclador.schemas import (
    AplicarNiveladoIn,
    AplicarNiveladoOut,
    NiveladoCodigoAltaIn,
    NiveladoCodigoIn,
    NiveladoCodigoOut,
    NiveladoCodigosOut,
    NomencladorNiveladoOut,
)

router = APIRouter()


@router.get("/", response_model=List[NomencladorNiveladoOut])
async def listar_nivelados(db: AsyncSession = Depends(get_db)):
    """Los nomencladores con su galeno, los códigos por nivel y las obras sociales
    que tienen ese galeno vigente."""
    return await nivelados.listar(db)


@router.get("/{slug}/codigos", response_model=NiveladoCodigosOut)
async def listar_codigos_nivelado(
    slug: str,
    nivel: Optional[int] = Query(None, ge=1),
    unidades: bool = Query(False, description="Sólo los de unidades fijas"),
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    return await nivelados.listar_codigos(
        db, slug, nivel=nivel, unidades=unidades, q=q, page=page, size=size,
    )


@router.post("/{slug}/codigos", response_model=NiveladoCodigoOut, status_code=201)
async def agregar_codigo_nivelado(slug: str, body: NiveladoCodigoAltaIn, db: AsyncSession = Depends(get_db)):
    out = await nivelados.agregar_codigo(db, slug, body)
    await db.commit()
    return out


@router.put("/{slug}/codigos/{nomenclador_id}", response_model=NiveladoCodigoOut)
async def actualizar_codigo_nivelado(
    slug: str, nomenclador_id: int, body: NiveladoCodigoIn, db: AsyncSession = Depends(get_db),
):
    out = await nivelados.actualizar_codigo(db, slug, nomenclador_id, body)
    await db.commit()
    return out


@router.delete("/{slug}/codigos/{nomenclador_id}", status_code=204)
async def quitar_codigo_nivelado(slug: str, nomenclador_id: int, db: AsyncSession = Depends(get_db)):
    await nivelados.quitar_codigo(db, slug, nomenclador_id)
    await db.commit()
    return Response(status_code=204)


@router.post("/{slug}/aplicar", response_model=AplicarNiveladoOut)
async def aplicar_nivelado(
    slug: str, body: AplicarNiveladoIn,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Da de alta los códigos del nomenclador en la obra social y les crea precio
    con el galeno de su nivel. Lo que ya tiene precio se saltea. Con `dry_run`
    (default) sólo muestra qué haría."""
    out = await nivelados.aplicar(
        db, slug, body.obra_social_nro, body.vigencia_desde,
        dry_run=body.dry_run, usuario=str(user.get("nro_socio", "")) or None,
    )
    if body.dry_run:
        await db.rollback()
    else:
        await db.commit()
    return out
