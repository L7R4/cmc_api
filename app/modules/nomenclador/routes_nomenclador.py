from typing import List, Optional

import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.deps import get_current_user_with_scopes_and_role
from app.db.database import get_db
from app.db.models import Especialidad
from app.db.models.nomenclador_cmc import (
    MedicoCodigoHabilitado,
    NomencladorCMC,
    NomencladorEspecialidad,
    Valor,
)
from app.modules.nomenclador.service import _especialidades_medico
from app.modules.nomenclador.schemas import (
    MedicoHabilitacionCreate,
    MedicoHabilitacionOut,
    MedicoHabilitacionUpdate,
    NomencladorConVariantesOut,
    NomencladorCreate,
    NomencladorEspecialidadCreate,
    NomencladorEspecialidadOut,
    NomencladorEspecialidadResumenOut,
    NomencladorOut,
    NomencladorUpdate,
)

router = APIRouter()


# ── Nomenclador CRUD ──────────────────────────────────────────────────────────

@router.get("/", response_model=List[NomencladorOut])
async def list_nomenclador(
    q: Optional[str] = Query(None),
    categoria: Optional[str] = Query(None),
    complejidad: Optional[str] = Query(None),
    activo: Optional[bool] = Query(True),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    dep=Depends(get_current_user_with_scopes_and_role),
):
    user, _scopes, role = dep

    stmt = select(NomencladorCMC)
    if q:
        stmt = stmt.where(
            NomencladorCMC.codigo.contains(q) | NomencladorCMC.descripcion.contains(q)
        )
    if categoria:
        stmt = stmt.where(NomencladorCMC.categoria == categoria)
    if complejidad:
        stmt = stmt.where(NomencladorCMC.complejidad == complejidad)
    if activo is not None:
        stmt = stmt.where(NomencladorCMC.activo == activo)

    # Rol "medico": solo ve los códigos que tiene habilitados. Replica la misma
    # precedencia que el gate de facturación (_validar_habilitacion_medico):
    #   permitido = NO inhabilitado Y (habilitado O sin_restriccion O match_especialidad)
    # - inhabilita/habilita: overrides individuales vigentes de nm_medico_codigo_habilitado
    #   (inhabilita gana sobre todo lo demás).
    # - sin_restriccion: código habilitado para todos.
    # - match_especialidad: nm_nomenclador_especialidad activa para alguna de sus
    #   NRO_ESPECIALIDAD*.
    # Otros roles (operador/admin) ven el catálogo completo.
    if role == "medico":
        hoy = datetime.date.today()
        especialidades = _especialidades_medico(user)

        vigencia_ok = and_(
            (MedicoCodigoHabilitado.vigencia_desde.is_(None))
            | (MedicoCodigoHabilitado.vigencia_desde <= hoy),
            (MedicoCodigoHabilitado.vigencia_hasta.is_(None))
            | (MedicoCodigoHabilitado.vigencia_hasta >= hoy),
        )

        def _override_vigente(tipo: str):
            return exists().where(
                (MedicoCodigoHabilitado.medico_id == user.ID)
                & (MedicoCodigoHabilitado.nomenclador_id == NomencladorCMC.id)
                & (MedicoCodigoHabilitado.tipo == tipo)
                & (MedicoCodigoHabilitado.activo == True)
                & vigencia_ok
            )

        inhabilitado = _override_vigente("inhabilita")
        habilitado = _override_vigente("habilita")
        esp_habilitada = exists().where(
            (NomencladorEspecialidad.nomenclador_id == NomencladorCMC.id)
            & (NomencladorEspecialidad.especialidad_id_colegio.in_(especialidades))
            & (NomencladorEspecialidad.activo == True)
        )

        stmt = stmt.where(
            ~inhabilitado,
            or_(
                habilitado,
                NomencladorCMC.sin_restriccion_especialidad == True,
                esp_habilitada,
            ),
        )

    stmt = stmt.offset((page - 1) * size).limit(size)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/codigos", response_model=List[str])
async def list_codigos(
    activo: Optional[bool] = Query(True),
    db: AsyncSession = Depends(get_db),
):
    """Solo los códigos del catálogo (para auto-detectar/validar en importaciones)."""
    stmt = select(NomencladorCMC.codigo)
    if activo is not None:
        stmt = stmt.where(NomencladorCMC.activo == activo)
    result = await db.execute(stmt)
    return [row[0] for row in result.all()]


@router.get("/especialidades", response_model=List[NomencladorEspecialidadResumenOut])
async def list_codigos_por_especialidad(
    q: Optional[str] = Query(None, description="Busca en código o descripción del nomenclador"),
    especialidad_id_colegio: Optional[int] = Query(None, description="Filtra por ID_COLEGIO_ESPE"),
    activo: Optional[bool] = Query(True),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    dep=Depends(get_current_user_with_scopes_and_role),
):
    """Vista tabla código↔especialidad: cada fila trae el código, su descripción y
    el nombre de la especialidad resuelto, en una sola llamada."""
    stmt = (
        select(
            NomencladorEspecialidad,
            NomencladorCMC.codigo,
            NomencladorCMC.descripcion,
        )
        .join(NomencladorCMC, NomencladorCMC.id == NomencladorEspecialidad.nomenclador_id)
    )
    if activo is not None:
        stmt = stmt.where(NomencladorEspecialidad.activo == activo)
    if especialidad_id_colegio is not None:
        stmt = stmt.where(
            NomencladorEspecialidad.especialidad_id_colegio == especialidad_id_colegio
        )
    if q:
        stmt = stmt.where(
            NomencladorCMC.codigo.contains(q) | NomencladorCMC.descripcion.contains(q)
        )
    stmt = stmt.order_by(NomencladorCMC.codigo).offset((page - 1) * size).limit(size)
    rows = (await db.execute(stmt)).all()

    # Resolver nombres (ID_COLEGIO_ESPE → especialidad.ESPECIALIDAD) con un solo
    # query, igual que medicos/padrones: ID_COLEGIO_ESPE no es PK, el join directo
    # podría multiplicar filas si estuviera duplicado.
    esp_ids = {ne.especialidad_id_colegio for ne, _cod, _desc in rows}
    nombres: dict[int, str] = {}
    if esp_ids:
        esp_rows = await db.execute(
            select(Especialidad.ID_COLEGIO_ESPE, Especialidad.ESPECIALIDAD)
            .where(Especialidad.ID_COLEGIO_ESPE.in_(esp_ids))
        )
        for id_colegio, nombre in esp_rows.all():
            nombres.setdefault(int(id_colegio), str(nombre))

    return [
        NomencladorEspecialidadResumenOut(
            id=ne.id,
            nomenclador_id=ne.nomenclador_id,
            codigo=codigo,
            descripcion=descripcion,
            especialidad_id_colegio=ne.especialidad_id_colegio,
            especialidad=nombres.get(ne.especialidad_id_colegio),
            activo=ne.activo,
            observacion=ne.observacion,
            created_at=ne.created_at,
        )
        for ne, codigo, descripcion in rows
    ]


@router.post("/", response_model=NomencladorOut, status_code=201)
async def create_nomenclador(body: NomencladorCreate, db: AsyncSession = Depends(get_db)):
    if body.proviene_de_id is not None:
        raiz = await db.get(NomencladorCMC, body.proviene_de_id)
        if not raiz:
            raise HTTPException(404, "Código raíz no encontrado")
    obj = NomencladorCMC(**body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.get("/{id}", response_model=NomencladorConVariantesOut)
async def get_nomenclador(id: int, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(NomencladorCMC)
        .where(NomencladorCMC.id == id)
        .options(selectinload(NomencladorCMC.variantes))
    )
    obj = (await db.execute(stmt)).scalar_one_or_none()
    if not obj:
        raise HTTPException(404, "Código no encontrado")
    return obj


@router.put("/{id}", response_model=NomencladorOut)
async def update_nomenclador(id: int, body: NomencladorUpdate, db: AsyncSession = Depends(get_db)):
    obj = await db.get(NomencladorCMC, id)
    if not obj:
        raise HTTPException(404, "Código no encontrado")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(obj, field, value)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.patch("/{id}/activar", response_model=NomencladorOut)
async def toggle_activo_nomenclador(
    id: int, activo: bool, db: AsyncSession = Depends(get_db)
):
    obj = await db.get(NomencladorCMC, id)
    if not obj:
        raise HTTPException(404, "Código no encontrado")
    obj.activo = activo
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/{id}", status_code=204)
async def delete_nomenclador(id: int, db: AsyncSession = Depends(get_db)):
    obj = await db.get(NomencladorCMC, id)
    if not obj:
        raise HTTPException(404, "Código no encontrado")
    stmt = select(Valor).where(Valor.nomenclador_id == id, Valor.estado == "activo").limit(1)
    if (await db.execute(stmt)).scalar_one_or_none():
        raise HTTPException(409, "El código tiene valores activos; ciérrelos antes de eliminarlo")
    stmt_variantes = select(NomencladorCMC).where(NomencladorCMC.proviene_de_id == id).limit(1)
    if (await db.execute(stmt_variantes)).scalar_one_or_none():
        raise HTTPException(409, "El código tiene variantes; elimínelas primero")
    await db.delete(obj)
    await db.commit()


# ── Especialidades habilitadas ────────────────────────────────────────────────

@router.get("/{id}/especialidades", response_model=List[NomencladorEspecialidadOut])
async def list_especialidades(id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(NomencladorEspecialidad).where(
        NomencladorEspecialidad.nomenclador_id == id,
        NomencladorEspecialidad.activo == True,
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/{id}/especialidades", response_model=NomencladorEspecialidadOut, status_code=201)
async def add_especialidad(
    id: int, body: NomencladorEspecialidadCreate, db: AsyncSession = Depends(get_db)
):
    obj = await db.get(NomencladorCMC, id)
    if not obj:
        raise HTTPException(404, "Código no encontrado")
    # Reactivar si ya existía soft-deleted
    stmt = select(NomencladorEspecialidad).where(
        NomencladorEspecialidad.nomenclador_id == id,
        NomencladorEspecialidad.especialidad_id_colegio == body.especialidad_id_colegio,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        existing.activo = True
        existing.observacion = body.observacion
        await db.commit()
        await db.refresh(existing)
        return existing
    ne = NomencladorEspecialidad(
        nomenclador_id=id,
        especialidad_id_colegio=body.especialidad_id_colegio,
        observacion=body.observacion,
    )
    db.add(ne)
    await db.commit()
    await db.refresh(ne)
    return ne


@router.patch("/{id}/especialidades/{esp_id}/activar", response_model=NomencladorEspecialidadOut)
async def toggle_especialidad(
    id: int, esp_id: int, activo: bool, db: AsyncSession = Depends(get_db)
):
    stmt = select(NomencladorEspecialidad).where(
        NomencladorEspecialidad.nomenclador_id == id,
        NomencladorEspecialidad.especialidad_id_colegio == esp_id,
    )
    obj = (await db.execute(stmt)).scalar_one_or_none()
    if not obj:
        raise HTTPException(404, "Habilitación no encontrada")
    obj.activo = activo
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/{id}/especialidades/{esp_id}", status_code=204)
async def delete_especialidad(id: int, esp_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(NomencladorEspecialidad).where(
        NomencladorEspecialidad.nomenclador_id == id,
        NomencladorEspecialidad.especialidad_id_colegio == esp_id,
    )
    obj = (await db.execute(stmt)).scalar_one_or_none()
    if not obj:
        raise HTTPException(404, "Habilitación no encontrada")
    await db.delete(obj)
    await db.commit()


# ── Habilitaciones por médico ─────────────────────────────────────────────────

@router.get("/{id}/habilitaciones_medico", response_model=List[MedicoHabilitacionOut])
async def list_habilitaciones_medico(
    id: int,
    activo: Optional[bool] = Query(True),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(MedicoCodigoHabilitado).where(
        MedicoCodigoHabilitado.nomenclador_id == id,
    )
    if activo is not None:
        stmt = stmt.where(MedicoCodigoHabilitado.activo == activo)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/{id}/habilitaciones_medico", response_model=MedicoHabilitacionOut, status_code=201)
async def create_habilitacion_medico(
    id: int, body: MedicoHabilitacionCreate, db: AsyncSession = Depends(get_db)
):
    if not await db.get(NomencladorCMC, id):
        raise HTTPException(404, "Código no encontrado")
    obj = MedicoCodigoHabilitado(nomenclador_id=id, **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.put("/{id}/habilitaciones_medico/{hab_id}", response_model=MedicoHabilitacionOut)
async def update_habilitacion_medico(
    id: int, hab_id: int, body: MedicoHabilitacionUpdate, db: AsyncSession = Depends(get_db)
):
    obj = await db.get(MedicoCodigoHabilitado, hab_id)
    if not obj or obj.nomenclador_id != id:
        raise HTTPException(404, "Habilitación no encontrada")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(obj, field, value)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.patch("/{id}/habilitaciones_medico/{hab_id}/activar", response_model=MedicoHabilitacionOut)
async def toggle_habilitacion_medico(
    id: int, hab_id: int, activo: bool, db: AsyncSession = Depends(get_db)
):
    obj = await db.get(MedicoCodigoHabilitado, hab_id)
    if not obj or obj.nomenclador_id != id:
        raise HTTPException(404, "Habilitación no encontrada")
    obj.activo = activo
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/{id}/habilitaciones_medico/{hab_id}", status_code=204)
async def delete_habilitacion_medico(id: int, hab_id: int, db: AsyncSession = Depends(get_db)):
    obj = await db.get(MedicoCodigoHabilitado, hab_id)
    if not obj or obj.nomenclador_id != id:
        raise HTTPException(404, "Habilitación no encontrada")
    await db.delete(obj)
    await db.commit()
