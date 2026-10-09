from typing import List, Literal, Optional

import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, get_current_user_with_scopes_and_role
from app.db.database import get_db
from app.db.models import Especialidad
from app.db.models.nomenclador_cmc import (
    CodigoObraSocial,
    MedicoCodigoHabilitado,
    NomencladorCMC,
    Valor,
    ValorEspecialidad,
)
from app.modules.nomenclador import alta_os, aplicar_plantilla, service
from app.modules.nomenclador.service import _especialidades_medico
from app.modules.nomenclador.schemas import (
    AplicarAltaIn,
    AplicarAltaOut,
    FichaCodigoOut,
    PlantillaEspecialidadesIn,
    PlantillaEspecialidadesOut,
    PropagarEspecialidadesIn,
    PropagarEspecialidadesOut,
    CodigoPorEspecialidadOut,
    MedicoHabilitacionCreate,
    MedicoHabilitacionOut,
    MedicoHabilitacionUpdate,
    NomencladorCreate,
    NomencladorDetalleOut,
    NomencladorOut,
    NomencladorUpdate,
)

router = APIRouter()


# ── Nomenclador CRUD ──────────────────────────────────────────────────────────

@router.get("/", response_model=List[NomencladorOut])
async def list_nomenclador(
    q: Optional[str] = Query(None),
    en_descripcion: bool = Query(
        False,
        description=(
            "Con `q`: busca también en la descripción. Primero los que empiezan con `q` "
            "en el código, después el resto en orden de código. Con `obra_social_nro` la "
            "descripción es la que esa obra social pactó (la del catálogo suele estar "
            "vacía), y es la que se devuelve."
        ),
    ),
    categoria: Optional[str] = Query(None),
    tipo: Optional[Literal[service.TIPOS_FILTRO]] = Query(
        None,
        description=(
            "Consulta / Practica / Honorarios individuales, por la categoría del catálogo. "
            "Sin categoría cuenta como Practica (ver `service.condicion_tipo`)."
        ),
    ),
    complejidad: Optional[str] = Query(None),
    obra_social_nro: Optional[int] = Query(
        None,
        description=(
            "Acota el listado del rol médico a lo que ese médico puede facturar en esa "
            "OS (ver más abajo). El catálogo en sí ya no distingue por obra social — "
            "todo código es del Colegio — así que para operador/admin este parámetro no "
            "cambia nada."
        ),
    ),
    activo: Optional[bool] = Query(
        None,
        description=(
            "Omitido = catálogo completo (activos e inactivos). El front de Gestión de "
            "Códigos manda `true`/`false` para sus filtros 'Activos'/'Inactivos' y omite "
            "el parámetro para 'Todos' — con un default distinto de `None` acá, 'Todos' "
            "terminaba mostrando sólo los activos igual."
        ),
    ),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    dep=Depends(get_current_user_with_scopes_and_role),
):
    user, _scopes, role = dep

    stmt = select(NomencladorCMC)
    q = q.strip() if q else q
    descripcion_os = bool(q and en_descripcion and obra_social_nro is not None)
    if q and en_descripcion:
        coincide = or_(NomencladorCMC.codigo.contains(q), NomencladorCMC.descripcion.contains(q))
        if descripcion_os:
            # La descripción vive por obra social (`nm_valores`, o el alta sin precio):
            # buscar solo en el catálogo no encuentra los códigos que lo tienen vacío.
            coincide = or_(
                coincide,
                exists().where(
                    Valor.nomenclador_id == NomencladorCMC.id,
                    Valor.obra_social_nro == obra_social_nro,
                    Valor.estado == "activo",
                    Valor.descripcion.contains(q),
                ),
                exists().where(
                    CodigoObraSocial.nomenclador_id == NomencladorCMC.id,
                    CodigoObraSocial.obra_social_nro == obra_social_nro,
                    CodigoObraSocial.descripcion.contains(q),
                ),
            )
        stmt = stmt.where(coincide).order_by(
            case((NomencladorCMC.codigo.startswith(q), 0), else_=1), NomencladorCMC.codigo,
        )
    elif q:
        stmt = stmt.where(NomencladorCMC.codigo.contains(q))
    if categoria:
        stmt = stmt.where(NomencladorCMC.categoria == categoria)
    if tipo:
        stmt = stmt.where(service.condicion_tipo(NomencladorCMC.categoria, tipo))
    if complejidad:
        stmt = stmt.where(NomencladorCMC.complejidad == complejidad)
    if activo is not None:
        stmt = stmt.where(NomencladorCMC.activo == activo)

    # Rol "medico": solo ve los códigos que tiene habilitados. Replica la misma
    # precedencia que el gate de facturación (_validar_habilitacion_medico):
    #   permitido = NO inhabilitado Y (habilitado O sin_restriccion O match_especialidad)
    # - inhabilita/habilita: overrides individuales vigentes de nm_medico_codigo_habilitado
    #   (inhabilita gana sobre todo lo demás).
    # - sin_restriccion / match_especialidad: viven en nm_valores / nm_valor_especialidad,
    #   por (obra_social_nro, código) — sin OS en contexto no hay nada que mirar ahí, así
    #   que solo entran habilita/inhabilita (más restrictivo, pero es lo correcto: sin OS
    #   no hay forma de saber qué código factura ese médico en cuál).
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

        if obra_social_nro is not None:
            # Deliberadamente más PERMISIVO que el gate de cotización
            # (_validar_habilitacion_medico): esto es un listado, el rechazo fino
            # ocurre al cotizar.
            sin_restriccion = exists().where(
                Valor.codigo == NomencladorCMC.codigo,
                Valor.obra_social_nro == obra_social_nro,
                Valor.estado == "activo",
                Valor.sin_restriccion_especialidad == True,
            )
            esp_habilitada = exists().where(
                ValorEspecialidad.codigo == NomencladorCMC.codigo,
                ValorEspecialidad.obra_social_nro == obra_social_nro,
                ValorEspecialidad.especialidad_id_colegio.in_(especialidades),
            )
            stmt = stmt.where(
                ~inhabilitado, or_(habilitado, sin_restriccion, esp_habilitada),
            )
        else:
            stmt = stmt.where(~inhabilitado, habilitado)

    stmt = stmt.offset((page - 1) * size).limit(size)
    filas = (await db.execute(stmt)).scalars().all()
    if not descripcion_os or not filas:
        return filas

    # Con obra social, el texto de cada código es el que esa OS pactó.
    ids = [n.id for n in filas]
    pactada = dict((await db.execute(
        select(Valor.nomenclador_id, func.max(Valor.descripcion))
        .where(
            Valor.obra_social_nro == obra_social_nro, Valor.nomenclador_id.in_(ids),
            Valor.estado == "activo", Valor.descripcion.is_not(None), Valor.descripcion != "",
        )
        .group_by(Valor.nomenclador_id)
    )).all())
    alta = dict((await db.execute(
        select(CodigoObraSocial.nomenclador_id, CodigoObraSocial.descripcion)
        .where(
            CodigoObraSocial.obra_social_nro == obra_social_nro,
            CodigoObraSocial.nomenclador_id.in_(ids),
            CodigoObraSocial.descripcion.is_not(None), CodigoObraSocial.descripcion != "",
        )
    )).all())
    salida = []
    for n in filas:
        out = NomencladorOut.model_validate(n)
        out.descripcion = pactada.get(n.id) or alta.get(n.id) or n.descripcion
        salida.append(out)
    return salida


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


@router.get("/especialidades", response_model=List[CodigoPorEspecialidadOut])
async def list_codigos_por_especialidad(
    obra_social_nro: int = Query(..., description="Obra social cuya habilitación se consulta"),
    q: Optional[str] = Query(None, description="Busca en el código"),
    especialidad_id_colegio: Optional[int] = Query(None, description="Filtra por ID_COLEGIO_ESPE"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    dep=Depends(get_current_user_with_scopes_and_role),
):
    """Vista tabla código↔especialidad PARA UNA OBRA SOCIAL: cada fila trae el código,
    su descripción pactada con esa OS y el nombre de la especialidad resuelto.

    Las especialidades pasaron a ser un dato por obra social (`nm_valor_especialidad`,
    ver el modal de Valores) — antes de la reestructura este listado mezclaba las
    reglas compartidas del Colegio con las propias de cada OS; ahora `obra_social_nro`
    es obligatorio porque ya no hay una versión "sin OS" que mostrar.
    """
    stmt = select(ValorEspecialidad).where(ValorEspecialidad.obra_social_nro == obra_social_nro)
    if especialidad_id_colegio is not None:
        stmt = stmt.where(ValorEspecialidad.especialidad_id_colegio == especialidad_id_colegio)
    if q:
        stmt = stmt.where(ValorEspecialidad.codigo.contains(q))
    stmt = stmt.order_by(ValorEspecialidad.codigo).offset((page - 1) * size).limit(size)
    filas = (await db.execute(stmt)).scalars().all()
    if not filas:
        return []

    codigos = {f.codigo for f in filas}
    esp_ids = {f.especialidad_id_colegio for f in filas}

    # Resolver nombres (ID_COLEGIO_ESPE → especialidad.ESPECIALIDAD) con un solo
    # query, igual que medicos/padrones: ID_COLEGIO_ESPE no es PK, el join directo
    # podría multiplicar filas si estuviera duplicado.
    nombres: dict[int, str] = {}
    if esp_ids:
        esp_rows = await db.execute(
            select(Especialidad.ID_COLEGIO_ESPE, Especialidad.ESPECIALIDAD)
            .where(Especialidad.ID_COLEGIO_ESPE.in_(esp_ids))
        )
        for id_colegio, nombre in esp_rows.all():
            nombres.setdefault(int(id_colegio), str(nombre))

    desc_rows = await db.execute(
        select(Valor.codigo, func.max(Valor.descripcion)).where(
            Valor.obra_social_nro == obra_social_nro,
            Valor.codigo.in_(codigos),
            Valor.estado == "activo",
            Valor.descripcion.is_not(None),
            Valor.descripcion != "",
        ).group_by(Valor.codigo)
    )
    descripciones = {c: d for c, d in desc_rows.all()}

    return [
        CodigoPorEspecialidadOut(
            codigo=f.codigo,
            descripcion=descripciones.get(f.codigo, ""),
            especialidad_id_colegio=f.especialidad_id_colegio,
            especialidad=nombres.get(f.especialidad_id_colegio),
            obra_social_nro=f.obra_social_nro,
        )
        for f in filas
    ]


async def _detalle(db: AsyncSession, obj: NomencladorCMC) -> NomencladorDetalleOut:
    out = NomencladorDetalleOut.model_validate(obj)
    out.especialidades = await aplicar_plantilla.leer_plantilla(db, obj.codigo)
    return out


@router.post("/", response_model=NomencladorDetalleOut, status_code=201)
async def create_nomenclador(body: NomencladorCreate, db: AsyncSession = Depends(get_db)):
    """Alta de un código del catálogo, con su plantilla de especialidades sugeridas.

    `codigo` es único. Sin este chequeo previo, repetir un número ya existente sube
    como IntegrityError y el handler global lo convierte en un 500 "Error al acceder
    a la base de datos", que no le dice al operador lo único que necesita saber: que
    ese número ya está tomado y por qué práctica.
    """
    try:
        obj = await aplicar_plantilla.crear_codigo(db, body)
        await db.commit()
    except aplicar_plantilla.CodigoExistente as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        await db.rollback()
        raise HTTPException(422, str(e))
    except IntegrityError:
        # Carrera: entre el SELECT de arriba y este commit, otra petición tomó el
        # mismo número. El chequeo previo da el mensaje bueno en el caso normal;
        # esto evita que la ventana angosta siga saliendo como 500.
        await db.rollback()
        raise HTTPException(
            409, f"El código {body.codigo} ya existe. Actualizá la lista y reintentá."
        )
    await db.refresh(obj)
    return await _detalle(db, obj)


@router.get("/{id}", response_model=NomencladorDetalleOut)
async def get_nomenclador(id: int, db: AsyncSession = Depends(get_db)):
    obj = await db.get(NomencladorCMC, id)
    if not obj:
        raise HTTPException(404, "Código no encontrado")
    return await _detalle(db, obj)


@router.put("/{id}", response_model=NomencladorDetalleOut)
async def update_nomenclador(id: int, body: NomencladorUpdate, db: AsyncSession = Depends(get_db)):
    obj = await db.get(NomencladorCMC, id)
    if not obj:
        raise HTTPException(404, "Código no encontrado")
    for field, value in body.model_dump(
        exclude_none=True, exclude={"especialidades", "descripcion"}
    ).items():
        setattr(obj, field, value)
    if "descripcion" in body.model_fields_set:
        obj.descripcion = (body.descripcion or "").strip() or None
    try:
        if obj.sin_restriccion_especialidad:
            # Con "sin restricción" la plantilla no aplica: se vacía.
            await aplicar_plantilla.reemplazar_plantilla(db, obj.codigo, [])
        elif body.especialidades is not None:
            await aplicar_plantilla.reemplazar_plantilla(db, obj.codigo, body.especialidades)
        await db.commit()
    except ValueError as e:
        await db.rollback()
        raise HTTPException(422, str(e))
    await db.refresh(obj)
    return await _detalle(db, obj)


@router.post("/{id}/aplicar-especialidades", response_model=AplicarAltaOut)
async def aplicar_especialidades(
    id: int, body: AplicarAltaIn,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """"Guardar y aplicar": en las O.S. que no tienen el código lo da de alta SIN
    PRECIO con la plantilla; en las que ya lo tienen, les suma las especialidades de
    la plantilla que les falten. Nunca crea precios (etapa 4). Éxito parcial."""
    nom = await db.get(NomencladorCMC, id)
    if not nom:
        raise HTTPException(404, "Código no encontrado")
    resultados = await alta_os.guardar_y_aplicar(
        db, nom, body.obra_social_nros, str(user.get("nro_socio", "")) or None,
    )
    await db.commit()
    return AplicarAltaOut(resultados=resultados)


@router.get("/{id}/ficha", response_model=FichaCodigoOut)
async def ficha_codigo(id: int, db: AsyncSession = Depends(get_db)):
    """Ficha del código: su estado (sin alta / sin precio / con precio / suspendido)
    en cada obra social activa, con quién lo factura y el precio vigente."""
    return await alta_os.ficha(db, id)


@router.put("/{id}/especialidades", response_model=PlantillaEspecialidadesOut)
async def guardar_plantilla_especialidades(
    id: int, body: PlantillaEspecialidadesIn, db: AsyncSession = Depends(get_db),
):
    """Etapa 2: quién puede facturar el código según el Colegio. No toca ninguna
    O.S. (para eso, `/especialidades/propagar`); se copia en cada alta nueva."""
    nom = await db.get(NomencladorCMC, id)
    if not nom:
        raise HTTPException(404, "Código no encontrado")
    try:
        await aplicar_plantilla.reemplazar_plantilla(
            db, nom.codigo, [] if body.sin_restriccion_especialidad else body.especialidades,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    nom.sin_restriccion_especialidad = body.sin_restriccion_especialidad
    await db.commit()
    return PlantillaEspecialidadesOut(
        nomenclador_id=nom.id, codigo=nom.codigo,
        sin_restriccion_especialidad=bool(nom.sin_restriccion_especialidad),
        especialidades=await aplicar_plantilla.leer_plantilla(db, nom.codigo),
    )


@router.post("/{id}/especialidades/propagar", response_model=PropagarEspecialidadesOut)
async def propagar_especialidades(
    id: int, body: PropagarEspecialidadesIn, db: AsyncSession = Depends(get_db),
):
    """"Actualizar en obras sociales": lleva la plantilla a las O.S. que ya tienen el
    código dado de alta. `agregar` suma lo nuevo; `igualar` además quita lo que
    sobra, también las especialidades con precio propio (ese precio se da de baja
    desde hoy). `dry_run` = vista previa."""
    nom = await db.get(NomencladorCMC, id)
    if not nom:
        raise HTTPException(404, "Código no encontrado")
    resultados = await alta_os.propagar_plantilla(
        db, nom, body.obra_social_nros, body.modo, body.dry_run,
    )
    if body.dry_run:
        await db.rollback()
    else:
        await db.commit()
    return PropagarEspecialidadesOut(dry_run=body.dry_run, modo=body.modo, resultados=resultados)


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
