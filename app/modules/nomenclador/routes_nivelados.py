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
    NiveladoEscalaIn,
    NiveladoEscalaOut,
    NiveladoEspecialidadesIn,
    NiveladoEstadoOsOut,
    NomencladorDeCodigoOut,
    NomencladorNiveladoOut,
    RecalcularNiveladoIn,
)

router = APIRouter()


@router.get("/", response_model=List[NomencladorNiveladoOut])
async def listar_nivelados(db: AsyncSession = Depends(get_db)):
    """Los nomencladores con su galeno, los códigos por nivel y las obras sociales
    que tienen ese galeno vigente."""
    return await nivelados.listar(db)


@router.get("/por_codigo", response_model=List[NomencladorDeCodigoOut])
async def nomencladores_de_un_codigo(nomenclador_id: int = Query(...), db: AsyncSession = Depends(get_db)):
    """Con qué nomencladores se puede facturar un código (para el alta de un valor)."""
    return await nivelados.nomencladores_de_codigo(db, nomenclador_id)


@router.put("/{slug}/especialidades", response_model=NiveladoEspecialidadesIn)
async def actualizar_especialidades_nivelado(
    slug: str, body: NiveladoEspecialidadesIn, db: AsyncSession = Depends(get_db),
):
    """A qué especialidades se aplica el nomenclador (vacío = todas las del código)."""
    ids = await nivelados.actualizar_especialidades(db, slug, body.especialidades)
    await db.commit()
    return NiveladoEspecialidadesIn(especialidades=ids)


@router.get("/{slug}/escala", response_model=NiveladoEscalaOut)
async def obtener_escala_nivelado(slug: str, db: AsyncSession = Depends(get_db)):
    """Unidades de honorarios y de ayudante de cada nivel del nomenclador."""
    return await nivelados.obtener_escala(db, slug)


@router.put("/{slug}/escala", response_model=NiveladoEscalaOut)
async def guardar_escala_nivelado(slug: str, body: NiveladoEscalaIn, db: AsyncSession = Depends(get_db)):
    """Reemplaza la escala. No toca valores: para llevarla a las obras sociales, `recalcular`."""
    out = await nivelados.guardar_escala(db, slug, body)
    await db.commit()
    return out


@router.get("/{slug}/estado_os", response_model=NiveladoEstadoOsOut)
async def estado_os_nivelado(
    slug: str, obra_social_nro: int = Query(...), db: AsyncSession = Depends(get_db),
):
    """Qué tiene la obra social del galeno del nomenclador: si falta, si está de otra cantidad de
    niveles (cambio 7 ↔ 10, con lo que se eliminaría) y sus precios y ajustes por nivel."""
    return await nivelados.estado_os(db, slug, obra_social_nro)


@router.post("/{slug}/recalcular")
async def recalcular_nivelado(
    slug: str, body: RecalcularNiveladoIn,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Vuelve a calcular los valores del nomenclador en todas las obras sociales que lo usan, con
    la escala actual y desde la vigencia indicada. Con `dry_run` (default) sólo cuenta."""
    out = await nivelados.recalcular(
        db, slug, body.vigencia_desde, dry_run=body.dry_run, usuario=str(user.get("nro_socio", "")) or None,
    )
    if body.dry_run:
        await db.rollback()
    else:
        await db.commit()
    return out


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
        dry_run=body.dry_run, usuario=str(user.get("nro_socio", "")) or None, galeno=body.galeno,
    )
    if body.dry_run:
        await db.rollback()
    else:
        await db.commit()
    return out
