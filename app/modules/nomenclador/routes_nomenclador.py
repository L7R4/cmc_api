from typing import List, Optional

import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user_with_scopes_and_role
from app.db.database import get_db
from app.db.models import Especialidad
from app.db.models.cmc_facturacion import DetalleFacturacionCMC
from app.db.models.nomenclador_cmc import (
    HistorialPrecioCodigo,
    MedicoCodigoHabilitado,
    NomencladorCMC,
    NomencladorEspecialidad,
    Valor,
)
from app.modules.nomenclador import service
from app.modules.nomenclador.service import _especialidades_medico
from app.modules.nomenclador.schemas import (
    DesacoplarOut,
    MedicoHabilitacionCreate,
    MedicoHabilitacionOut,
    MedicoHabilitacionUpdate,
    NomencladorCreate,
    NomencladorEspecialidadCreate,
    NomencladorEspecialidadOut,
    NomencladorEspecialidadResumenOut,
    NomencladorOut,
    NomencladorUpdate,
)

router = APIRouter()


def _cond_os_especialidad(obra_social_nro: Optional[int]):
    """Identifica UNA regla de especialidad: la compartida (OS en NULL) o la propia de
    una obra social. Sin esto, `(codigo, especialidad)` dejó de ser identidad única."""
    if obra_social_nro is None:
        return NomencladorEspecialidad.obra_social_nro.is_(None)
    return NomencladorEspecialidad.obra_social_nro == obra_social_nro


# ── Nomenclador CRUD ──────────────────────────────────────────────────────────

@router.get("/", response_model=List[NomencladorOut])
async def list_nomenclador(
    q: Optional[str] = Query(None),
    categoria: Optional[str] = Query(None),
    complejidad: Optional[str] = Query(None),
    obra_social_nro: Optional[int] = Query(
        None,
        description=(
            "Acota a lo que ve esa obra social: sus códigos propios + los compartidos "
            "del Colegio. Omitido = catálogo completo, incluidos los propios de todas "
            "las obras sociales (uso administrativo)."
        ),
    ),
    activo: Optional[bool] = Query(True),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    dep=Depends(get_current_user_with_scopes_and_role),
):
    user, _scopes, role = dep

    stmt = select(NomencladorCMC)
    if obra_social_nro is not None:
        stmt = stmt.where(service.filtro_pertenencia(obra_social_nro))
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
        # Con OS en contexto se acotan las reglas a las visibles por esa obra social
        # (propias + compartidas). Es deliberadamente más PERMISIVO que el gate de
        # cotización, que además aplica precedencia (las propias reemplazan a las
        # compartidas): esto es un listado, el rechazo fino ocurre al cotizar.
        cond_esp = [
            NomencladorEspecialidad.nomenclador_id == NomencladorCMC.id,
            NomencladorEspecialidad.especialidad_id_colegio.in_(especialidades),
            NomencladorEspecialidad.activo == True,
        ]
        if obra_social_nro is not None:
            cond_esp.append(or_(
                NomencladorEspecialidad.obra_social_nro.is_(None),
                NomencladorEspecialidad.obra_social_nro == obra_social_nro,
            ))
        esp_habilitada = exists().where(*cond_esp)

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
    obj = NomencladorCMC(**body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.post("/{id}/desacoplar/{obra_social_nro}", response_model=DesacoplarOut, status_code=201)
async def desacoplar_por_obra_social(
    id: int, obra_social_nro: int, db: AsyncSession = Depends(get_db)
):
    """Separa un código compartido en una fila propia de una obra social.

    Se usa cuando se descubre que el mismo número nombra prácticas distintas según la
    OS (o cuando una obra social necesita su propia categoría o grilla de
    especialidades). Clona la fila del catálogo con `obra_social_nro` seteado y le
    repunta todo lo que ya era de esa OS — valores, historial de precios y
    prestaciones ya facturadas —, de modo que la historia siga apuntando a la práctica
    con la que se cotizó. El precio no cambia: solo cambia de qué fila cuelga.

    La fila nueva nace como copia; el operador después le edita descripción, categoría
    y especialidades.
    """
    origen = await db.get(NomencladorCMC, id)
    if not origen:
        raise HTTPException(404, "Código no encontrado")
    if origen.obra_social_nro is not None:
        raise HTTPException(
            409,
            f"El código {origen.codigo} ya es propio de la OS {origen.obra_social_nro}; "
            "solo se desacopla un código compartido.",
        )

    ya_existe = (
        await db.execute(
            select(NomencladorCMC).where(
                NomencladorCMC.codigo == origen.codigo,
                NomencladorCMC.obra_social_nro == obra_social_nro,
            )
        )
    ).scalar_one_or_none()
    if ya_existe:
        raise HTTPException(
            409,
            f"La OS {obra_social_nro} ya tiene una fila propia para el código "
            f"{origen.codigo} (id {ya_existe.id}).",
        )

    propio = NomencladorCMC(
        codigo=origen.codigo,
        obra_social_nro=obra_social_nro,
        codigo_nacional=origen.codigo_nacional,
        descripcion=origen.descripcion,
        categoria=origen.categoria,
        complejidad=origen.complejidad,
        sin_restriccion_especialidad=origen.sin_restriccion_especialidad,
        unidades_honorarios=origen.unidades_honorarios,
        unidades_ayudante=origen.unidades_ayudante,
        unidades_gastos=origen.unidades_gastos,
        activo=origen.activo,
        observacion=f"Desacoplado de nm_nomenclador.id={origen.id} para la OS {obra_social_nro}",
    )
    db.add(propio)
    await db.flush()

    especialidades = (
        await db.execute(
            select(NomencladorEspecialidad).where(
                NomencladorEspecialidad.nomenclador_id == origen.id
            )
        )
    ).scalars().all()
    for esp in especialidades:
        db.add(
            NomencladorEspecialidad(
                nomenclador_id=propio.id,
                especialidad_id_colegio=esp.especialidad_id_colegio,
                activo=esp.activo,
                observacion=esp.observacion,
            )
        )

    # Todo lo que ya era de esta OS pasa a colgar de la fila propia.
    res_valores = await db.execute(
        update(Valor)
        .where(Valor.nomenclador_id == origen.id, Valor.obra_social_nro == obra_social_nro)
        .values(nomenclador_id=propio.id)
    )
    res_historial = await db.execute(
        update(HistorialPrecioCodigo)
        .where(
            HistorialPrecioCodigo.nomenclador_id == origen.id,
            HistorialPrecioCodigo.obra_social_nro == obra_social_nro,
        )
        .values(nomenclador_id=propio.id)
    )
    # `cod_obr` es varchar en la tabla legacy de facturación.
    res_detalle = await db.execute(
        update(DetalleFacturacionCMC)
        .where(
            DetalleFacturacionCMC.nomenclador_id == origen.id,
            DetalleFacturacionCMC.cod_obr == str(obra_social_nro),
        )
        .values(nomenclador_id=propio.id)
    )

    await db.commit()
    await db.refresh(propio)
    return DesacoplarOut(
        nomenclador=NomencladorOut.model_validate(propio),
        origen_id=origen.id,
        especialidades_clonadas=len(especialidades),
        valores_repuntados=res_valores.rowcount or 0,
        historial_repuntado=res_historial.rowcount or 0,
        prestaciones_repuntadas=res_detalle.rowcount or 0,
    )


@router.get("/{id}", response_model=NomencladorOut)
async def get_nomenclador(id: int, db: AsyncSession = Depends(get_db)):
    obj = await db.get(NomencladorCMC, id)
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
    await db.delete(obj)
    await db.commit()


# ── Especialidades habilitadas ────────────────────────────────────────────────

@router.get("/{id}/especialidades", response_model=List[NomencladorEspecialidadOut])
async def list_especialidades(
    id: int,
    obra_social_nro: Optional[int] = Query(
        None,
        description=(
            "Acota a las reglas visibles por esa OS (las propias + las compartidas). "
            "Omitido = todas, incluidas las propias de otras obras sociales."
        ),
    ),
    db: AsyncSession = Depends(get_db),
):
    """Especialidades habilitadas para el código.

    Una fila con `obra_social_nro` es una regla PROPIA de esa obra social y, cuando
    existe, reemplaza a las compartidas para esa OS (no se suma) — ver
    `service.especialidades_habilitadas_de`.
    """
    stmt = select(NomencladorEspecialidad).where(
        NomencladorEspecialidad.nomenclador_id == id,
        NomencladorEspecialidad.activo == True,
    )
    if obra_social_nro is not None:
        stmt = stmt.where(or_(
            NomencladorEspecialidad.obra_social_nro.is_(None),
            NomencladorEspecialidad.obra_social_nro == obra_social_nro,
        ))
    stmt = stmt.order_by(NomencladorEspecialidad.obra_social_key.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/{id}/especialidades", response_model=NomencladorEspecialidadOut, status_code=201)
async def add_especialidad(
    id: int, body: NomencladorEspecialidadCreate, db: AsyncSession = Depends(get_db)
):
    obj = await db.get(NomencladorCMC, id)
    if not obj:
        raise HTTPException(404, "Código no encontrado")
    # Reactivar si ya existía soft-deleted. La identidad incluye la OS: la regla
    # compartida y la propia de una obra social son filas distintas.
    stmt = select(NomencladorEspecialidad).where(
        NomencladorEspecialidad.nomenclador_id == id,
        NomencladorEspecialidad.especialidad_id_colegio == body.especialidad_id_colegio,
        _cond_os_especialidad(body.obra_social_nro),
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
        obra_social_nro=body.obra_social_nro,
        observacion=body.observacion,
    )
    db.add(ne)
    await db.commit()
    await db.refresh(ne)
    return ne


@router.patch("/{id}/especialidades/{esp_id}/activar", response_model=NomencladorEspecialidadOut)
async def toggle_especialidad(
    id: int,
    esp_id: int,
    activo: bool,
    obra_social_nro: Optional[int] = Query(
        None, description="Regla propia de esa OS; omitido = la regla compartida"
    ),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(NomencladorEspecialidad).where(
        NomencladorEspecialidad.nomenclador_id == id,
        NomencladorEspecialidad.especialidad_id_colegio == esp_id,
        _cond_os_especialidad(obra_social_nro),
    )
    obj = (await db.execute(stmt)).scalar_one_or_none()
    if not obj:
        raise HTTPException(404, "Habilitación no encontrada")
    obj.activo = activo
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/{id}/especialidades/{esp_id}", status_code=204)
async def delete_especialidad(
    id: int,
    esp_id: int,
    obra_social_nro: Optional[int] = Query(
        None, description="Regla propia de esa OS; omitido = la regla compartida"
    ),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(NomencladorEspecialidad).where(
        NomencladorEspecialidad.nomenclador_id == id,
        NomencladorEspecialidad.especialidad_id_colegio == esp_id,
        _cond_os_especialidad(obra_social_nro),
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
