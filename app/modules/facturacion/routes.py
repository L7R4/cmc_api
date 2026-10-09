import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.auth.ownership import filtro_socio, socio_objetivo
from app.common.files import url_archivo
from app.core.config import settings
from app.db.database import get_db
from app.db.models import ListadoMedico, NomencladorCMC, ObrasSociales
from app.modules.facturacion import recotizar as recotizar_mod, revalorizar as revalorizar_mod, service
from app.modules.facturacion.schemas import (
    AutorizacionExistente,
    ActividadEventoOut,
    AfiliadoCreate,
    AfiliadoRead,
    AfiliadoEditado,
    AfiliadoUpdate,
    AvanzarPeriodoMedicoPayload,
    AvanzarPeriodoMedicoResponse,
    CargaPorUsuarioOut,
    CerrarPeriodosVencidosResponse,
    CierresPorUsuarioOut,
    CierreDoctorPayload,
    CierreDoctorResponse,
    CierrePreviewResponse,
    CierreResponse,
    ClinicaBuscarOut,
    ClinicaCreate,
    ClinicaUpdate,
    CodigoHabilitadoOut,
    ComplementoCreate,
    FacturaDetalleOut,
    FacturaRead,
    GuardadoResponse,
    MedicoBuscarOut,
    MoverPeriodoPayload,
    MoverPeriodoResponse,
    PeriodoActivoResponse,
    PeriodoMedicoPunteroOut,
    PrecioResponse,
    SetPeriodoMedicoPayload,
    SetPeriodoMedicoResponse,
    PrestacionesComplementariaCreate,
    PrestacionesCreate,
    PrestacionFichaOut,
    PrestacionRead,
    PrestacionesRevisadoUpdate,
    PrestacionUpdate,
    PublicarPeriodoPayload,
    PublicarPeriodoResponse,
    PeriodoPropioOut,
)

router = APIRouter()


def _usuario(user: dict) -> str:
    return str(user["nro_socio"])


# ── Grupo A — Autocomplete ───────────────────────────────────────────────────
@router.get("/medicos", response_model=list[MedicoBuscarOut])
async def buscar_medicos(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    return await service.buscar_medicos(db, q, limit)


@router.get("/medicos/todos", response_model=list[MedicoBuscarOut])
async def listar_medicos_todos(db: AsyncSession = Depends(get_db)):
    """Precarga completa (médicos + clínicas) para el formulario de Carga de
    Facturación: el front la pide una sola vez al entrar y filtra en memoria en
    vez de pegarle a `/medicos` en cada tecleo. Ver `service.listar_medicos_todos`."""
    return await service.listar_medicos_todos(db)


@router.get("/clinicas", response_model=list[ClinicaBuscarOut])
async def buscar_clinicas(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    """Autocomplete de clínicas/organizaciones — mismo `listado_medico` que
    `/medicos`, filtrado por `es_organizacion=1`."""
    return await service.buscar_clinicas(db, q, limit)


@router.get("/clinicas/todas", response_model=list[ClinicaBuscarOut])
async def listar_clinicas_todas(db: AsyncSession = Depends(get_db)):
    """Precarga completa (~100-150 filas) para el formulario de Carga de
    Facturación — ver `/medicos/todos` y `/obras-sociales/todas`."""
    return await service.listar_clinicas_todas(db)


@router.post("/clinicas", response_model=ClinicaBuscarOut, status_code=status.HTTP_201_CREATED)
async def crear_clinica(
    payload: ClinicaCreate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Alta rápida de clínica — solo el nombre; el resto queda en su default y
    `es_organizacion=1` fijo. Mismo patrón que `POST /afiliados`."""
    return await service.crear_clinica(db, payload)


@router.patch("/clinicas/{cod}", response_model=ClinicaBuscarOut)
async def actualizar_clinica(
    cod: int, payload: ClinicaUpdate, db: AsyncSession = Depends(get_db),
):
    """Edición de la clínica (lápiz de la carga): el nombre."""
    return await service.actualizar_clinica(db, cod, payload)


@router.delete("/clinicas/{cod}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_clinica(
    cod: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Borra una clínica. 404 si no existe; 409 si tiene prestaciones no anuladas
    que la referencian (hay que anularlas antes)."""
    await service.eliminar_clinica(db, cod)


@router.get("/obras-sociales")
async def buscar_obras_sociales(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=20),
    solo_activas: bool = Query(
        True,
        description="Excluye las dadas de baja (MARCA='N'). false solo para resolver el "
                    "nombre de prestaciones ya cargadas, nunca para elegir una OS.",
    ),
    db: AsyncSession = Depends(get_db),
):
    O = ObrasSociales
    cond = O.OBRA_SOCIAL.ilike(f"%{q}%")
    if q.isdigit():
        cond = or_(O.NRO_OBRASOCIAL == int(q), cond)
    stmt = select(O).where(cond)
    if solo_activas:
        stmt = stmt.where(O.MARCA != "N")
    rows = (
        await db.execute(stmt.limit(limit))
    ).scalars().all()
    return [
        {"id": o.ID, "nro_obra_social": o.NRO_OBRASOCIAL, "nombre": o.OBRA_SOCIAL}
        for o in rows
    ]


@router.get("/obras-sociales/todas")
async def listar_obras_sociales_todas(db: AsyncSession = Depends(get_db)):
    """Precarga completa (~140 filas) para el mismo formulario — ver
    `/medicos/todos`."""
    O = ObrasSociales
    rows = (
        await db.execute(select(O).where(O.MARCA != "N").order_by(O.OBRA_SOCIAL))
    ).scalars().all()
    return [
        {"id": o.ID, "nro_obra_social": o.NRO_OBRASOCIAL, "nombre": o.OBRA_SOCIAL}
        for o in rows
    ]


@router.get("/nomenclador")
async def buscar_nomenclador(
    q: str = Query(..., min_length=1),
    cod_obra: str = Query(..., description="Obra social en la que se está cargando (cod_obr)"),
    limit: int = Query(20, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    """Autocomplete de códigos para una obra social.

    `cod_obra` es obligatorio: el mismo código puede ser una práctica del Colegio o una
    propia de la obra social, con descripciones distintas. Devuelve los códigos
    compartidos más los propios de esa OS, con el texto que usa esa obra social.
    """
    return await service.buscar_nomenclador(db, q, cod_obra, limit)


@router.get("/medico/{nro_socio}/codigos-habilitados", response_model=list[CodigoHabilitadoOut])
async def codigos_habilitados_medico(
    nro_socio: str,
    q: Optional[str] = Query(None, description="Filtro por código o descripción"),
    cod_obra: Optional[str] = Query(
        None, description="Obra social en la que se está cargando (cod_obr) — si se "
        "manda, la descripción de cada código es la que esa OS pactó en su nm_valores"
    ),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Códigos de nomenclador que el médico puede facturar: por especialidad +
    excepciones individuales + códigos sin restricción de especialidad — mismo
    alcance que evalúa el sistema al cargar una prestación.

    El `nro_socio` del path se valida contra el del token: un prestador solo
    consulta los propios. El personal del Colegio necesita `medico:leer`.
    """
    objetivo = socio_objetivo(user, int(nro_socio))
    return await service.codigos_habilitados_medico(db, str(objetivo), q, cod_obra)


# ── Grupo A2 — Afiliados ─────────────────────────────────────────────────────
@router.get("/afiliados", response_model=list[AfiliadoRead])
async def buscar_afiliados(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    """Autocomplete de afiliados por nombre, DNI o nro de afiliado."""
    return await service.buscar_afiliados(db, q, limit)


# `{dni:path}` en vez de `{dni}` porque el identificador puede ser un nro de afiliado
# con barra ("1231233/00") — el converter por defecto no matchea `/` y devolvía 404.
@router.get("/afiliados/{dni:path}", response_model=AfiliadoRead)
async def get_afiliado(dni: str, db: AsyncSession = Depends(get_db)):
    afiliado = await service.get_afiliado_by_dni(db, dni)
    if not afiliado:
        raise HTTPException(404, f"Afiliado '{dni}' no encontrado")
    return afiliado


@router.post("/afiliados", response_model=AfiliadoRead, status_code=status.HTTP_201_CREATED)
async def crear_afiliado(
    payload: AfiliadoCreate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await service.crear_afiliado(db, payload, _usuario(user))


# `{dni:path}` por el mismo motivo que el GET de arriba: el identificador puede
# traer barras ("1231233/00").
@router.delete("/afiliados/{dni:path}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_afiliado(
    dni: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Borra un afiliado del padrón. 404 si no existe; 409 si tiene prestaciones
    no anuladas que lo referencian (hay que anularlas antes)."""
    await service.eliminar_afiliado(db, dni)


# Por id y en otra ruta: un afiliado puede no tener número, y `/afiliados/{dni:path}`
# se tragaría cualquier sufijo ("/afiliados/id/5").
@router.patch("/afiliados-id/{afiliado_id}", response_model=AfiliadoEditado)
async def actualizar_afiliado(
    afiliado_id: int, payload: AfiliadoUpdate, db: AsyncSession = Depends(get_db),
):
    """Edición del afiliado (lápiz de la carga). Corrige también sus prestaciones no
    anuladas de facturas abiertas y devuelve cuántas."""
    af, n = await service.actualizar_afiliado(db, afiliado_id, payload)
    return AfiliadoEditado.model_validate(af).model_copy(update={"prestaciones_actualizadas": n})


@router.delete("/afiliados-id/{afiliado_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_afiliado_por_id(afiliado_id: int, db: AsyncSession = Depends(get_db)):
    """Baja por id. 409 si tiene prestaciones no anuladas."""
    await service.eliminar_afiliado_por_id(db, afiliado_id)


# ── Grupo B — Período y precio ───────────────────────────────────────────────
@router.get("/periodo-activo", response_model=PeriodoActivoResponse)
async def periodo_activo(
    cod_obra: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    periodo = await service.get_periodo_activo(db, cod_obra)
    return PeriodoActivoResponse(
        cod_obra=cod_obra, periodo=periodo, periodo_label=service.periodo_label(periodo)
    )


@router.get("/nomenclador/precio", response_model=PrecioResponse)
async def precio_nomenclador(
    cod_medico: str = Query(...),
    cod_obra: str = Query(...),
    codigo: str = Query(...),
    fecha: Optional[datetime.date] = Query(None),
    via: str = Query("T", description="T = tradicional, L = laparoscópica"),
    db: AsyncSession = Depends(get_db),
):
    medico = await service.check_medico_activo(db, cod_medico)
    fecha = service.fecha_para_precio(fecha)
    return await service.resolver_precio(db, cod_obra, medico, codigo, fecha, via=via, con_admision_via=True)


# ── Facturas / períodos ──────────────────────────────────────────────────────
@router.get("/facturas", response_model=list[FacturaRead])
async def listar_facturas(
    response: Response,
    cod_obra: Optional[str] = Query(None, description="Filtro por obra social (cod_obr)"),
    periodo: Optional[str] = Query(None, description="Filtro por período (YYYYMM)"),
    usuario: Optional[str] = Query(None, description="Filtro por operador (usuario)"),
    estado: Optional[str] = Query(None, description="Filtro por estado"),
    solo_complementos: Optional[bool] = Query(
        None,
        description="true = solo facturas complementarias (version>1); "
                    "false = solo originales (version=1); omitido = todas",
    ),
    q: Optional[str] = Query(None, description="Búsqueda por nro de factura"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Facturas/períodos (filas de `facturacion`) de la más reciente a la más
    antigua (por `created`). Soporta filtros por obra social, período, operador,
    estado, versión (`solo_complementos`), búsqueda por nro de factura y paginación."""
    rows, total = await service.listar_facturas(
        db,
        cod_obra=cod_obra, periodo=periodo, usuario=usuario,
        estado=estado, solo_complementos=solo_complementos, q=q,
        limit=limit, offset=offset,
    )
    # Batch de nombres (usuario = NRO_SOCIO de quien creó/cerró la cabecera) para
    # mostrar quién cerró cada factura, sin N+1.
    nros: set[int] = set()
    for row in rows:
        for valor in (row.usuario, row.creado_por):
            try:
                nros.add(int(valor))
            except (TypeError, ValueError):
                continue
    nombres: dict[str, str] = {}
    if nros:
        med_rows = (await db.execute(
            select(ListadoMedico.NRO_SOCIO, ListadoMedico.NOMBRE).where(
                ListadoMedico.NRO_SOCIO.in_(nros)
            )
        )).all()
        nombres = {str(nro_socio): nombre for nro_socio, nombre in med_rows}

    # Nombre de cada obra social, también en batch (la lista lo muestra junto al número).
    nros_os: set[int] = set()
    for row in rows:
        try:
            nros_os.add(int(row.cod_obr))
        except (TypeError, ValueError):
            continue
    nombres_os: dict[int, str] = {}
    if nros_os:
        os_rows = (await db.execute(
            select(ObrasSociales.NRO_OBRASOCIAL, ObrasSociales.OBRA_SOCIAL).where(
                ObrasSociales.NRO_OBRASOCIAL.in_(nros_os)
            )
        )).all()
        nombres_os = {int(nro): (nombre or "").strip() for nro, nombre in os_rows if nombre}

    # Importe en vivo para las cabeceras abiertas (normales o complementos): el
    # `importe` persistido solo se escribe al cerrar, mientras está abierto vale 0.
    importes_abiertos = await service.calcular_importes_abiertos(db, rows)
    # Estado de publicación en vivo (cod_obr+periodo, cualquier versión) — ver
    # `service.calcular_publicado`.
    publicado_por_id = await service.calcular_publicado(db, rows)

    out: list[FacturaRead] = []
    for row in rows:
        factura = FacturaRead.model_validate(row)
        factura.documento_url = url_archivo(factura.documento_url)
        if factura.periodo:
            factura.periodo_label = service.periodo_label(factura.periodo)
        if factura.usuario:
            factura.usuario_nombre = nombres.get(str(factura.usuario))
        if factura.creado_por:
            factura.creado_por_nombre = nombres.get(str(factura.creado_por))
        if factura.id_prestaciones in importes_abiertos:
            factura.importe = importes_abiertos[factura.id_prestaciones]
        factura.publicado = publicado_por_id.get(factura.id_prestaciones, False)
        try:
            factura.nombre_obra_social = nombres_os.get(int(factura.cod_obr)) or None
        except (TypeError, ValueError):
            pass
        out.append(factura)
    response.headers["X-Total-Count"] = str(total)
    response.headers["Content-Range"] = f"facturas {offset}-{offset + len(rows)}/{total}"
    return out


@router.get("/facturas/{id}/detalle", response_model=FacturaDetalleOut)
async def factura_detalle(id: int, db: AsyncSession = Depends(get_db)):
    """Detalle de una factura: sus prestaciones (misma OS+período, sin anuladas)
    agrupadas por prestador, con totales por prestador y de la factura."""
    return await service.obtener_factura_detalle(db, id)


# ── Registro de facturación (auditoría administrativa, scope facturacion:registro) ──
@router.get("/registro/carga-por-usuario", response_model=list[CargaPorUsuarioOut])
async def registro_carga_por_usuario(
    cod_obra: Optional[str] = Query(None),
    periodo: Optional[str] = Query(None, description="YYYYMM"),
    desde: Optional[datetime.date] = Query(None),
    hasta: Optional[datetime.date] = Query(None),
    limit: int = Query(200, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Ranking de prestaciones cargadas por operador del Colegio en un
    período/rango — nunca incluye lo que carga un médico desde su portal."""
    return await service.carga_por_usuario(
        db, cod_obra=cod_obra, periodo=periodo, desde=desde, hasta=hasta, limit=limit,
    )


@router.get("/registro/cierres-por-usuario", response_model=list[CierresPorUsuarioOut])
async def registro_cierres_por_usuario(
    cod_obra: Optional[str] = Query(None),
    periodo: Optional[str] = Query(None, description="YYYYMM"),
    desde: Optional[datetime.date] = Query(None),
    hasta: Optional[datetime.date] = Query(None),
    limit: int = Query(200, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Ranking de facturas cerradas por operador en un período/rango."""
    return await service.cierres_por_usuario(
        db, cod_obra=cod_obra, periodo=periodo, desde=desde, hasta=hasta, limit=limit,
    )


@router.get("/registro/actividad", response_model=list[ActividadEventoOut])
async def registro_actividad(
    cod_obra: Optional[str] = Query(None),
    periodo: Optional[str] = Query(None, description="YYYYMM"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Feed de actividad administrativa reciente: cierres + cargas del Colegio,
    intercalados por fecha descendente."""
    return await service.actividad_reciente(db, cod_obra=cod_obra, periodo=periodo, limit=limit)


# ── Grupo C — Prestaciones ───────────────────────────────────────────────────
@router.get("/prestaciones/recientes", response_model=list[PrestacionRead], response_model_by_alias=False)
async def prestaciones_recientes(
    cod_obra: str = Query(...),
    usuario: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    return await service.prestaciones_recientes(db, cod_obra, usuario)


@router.get("/autorizaciones/existentes", response_model=list[AutorizacionExistente])
async def autorizaciones_existentes(
    cod_obra: str = Query(...),
    autorizacion: str = Query(..., min_length=1, max_length=30),
    excluir_id: Optional[int] = Query(None, description="Prestación que se está editando"),
    db: AsyncSession = Depends(get_db),
):
    """¿Ese Nº de autorización ya está cargado en la O.S.? En qué períodos."""
    return await service.autorizacion_existente(db, cod_obra, autorizacion, excluir_id)


@router.get("/prestaciones", response_model=list[PrestacionRead], response_model_by_alias=False)
async def listar_prestaciones(
    response: Response,
    id: Optional[int] = Query(None, description="ID exacto de la prestación (PK)"),
    cod_obra: Optional[str] = Query(None),
    periodo: Optional[str] = Query(None),
    cod_medico: Optional[str] = Query(None),
    cod_nomenclador: Optional[str] = Query(None),
    estado: Optional[str] = Query(None),
    tipo: Optional[str] = Query(None),
    grupo_equipo_id: Optional[int] = Query(None),
    dni_paciente: Optional[str] = Query(None),
    nombre_paciente: Optional[str] = Query(None),
    fecha_desde: Optional[datetime.date] = Query(None),
    fecha_hasta: Optional[datetime.date] = Query(None),
    revisado: Optional[bool] = Query(None, description="Filtro por checkbox de auditoría"),
    q: Optional[str] = Query(None, description="Búsqueda libre: médico, código, paciente, nro_orden"),
    orden_o_autorizacion: Optional[str] = Query(
        None,
        description="Busca el texto en `nro_orden` O en `autorizacion`. Segundo campo "
                    "del buscador de la pantalla de consulta; se combina con AND con "
                    "el resto de los filtros (incluido `q`).",
    ),
    solo_facturas_abiertas: bool = Query(
        False,
        description="Devuelve sólo prestaciones cuya cabecera de facturación sigue "
                    "abierta, mirando `facturacion.estado` en vez del `estado` copiado "
                    "en la prestación. Usar en la tabla del formulario de carga.",
    ),
    publicado: Optional[bool] = Query(
        None,
        description="Filtro por visibilidad hacia el médico. true = sólo lo que el "
                    "Colegio publicó ('Mi recepción'); omitido = todo, publicado o no.",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    # Sin `medico:leer`, el filtro se fuerza al médico del token: un prestador
    # que omite `cod_medico` ve lo suyo, no lo de todos. Con el scope
    # administrativo, `None` sigue significando "sin filtro".
    _socio = filtro_socio(user, int(cod_medico) if cod_medico else None)
    cod_medico = str(_socio) if _socio is not None else None

    rows, total = await service.listar_prestaciones(
        db,
        prestacion_id=id,
        cod_obra=cod_obra, periodo=periodo, cod_medico=cod_medico,
        cod_nomenclador=cod_nomenclador, estado=estado,
        tipo=tipo, grupo_equipo_id=grupo_equipo_id,
        dni_paciente=dni_paciente, nombre_paciente=nombre_paciente,
        fecha_desde=fecha_desde, fecha_hasta=fecha_hasta, revisado=revisado, q=q,
        orden_o_autorizacion=orden_o_autorizacion,
        solo_facturas_abiertas=solo_facturas_abiertas,
        publicado=publicado,
        limit=limit, offset=offset,
    )
    response.headers["X-Total-Count"] = str(total)
    response.headers["Content-Range"] = f"prestaciones {offset}-{offset + len(rows)}/{total}"
    return rows


@router.post("/prestaciones", response_model=GuardadoResponse, status_code=status.HTTP_201_CREATED)
async def crear_prestaciones(
    payload: PrestacionesCreate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Carga del **colegio**. Por defecto el período es el automático (último cerrado +
    1). Si el payload trae `periodo` (YYYYMM, botón "editar período"), se carga en ese
    período — útil para saltar meses sin movimiento. Debe ser >= el automático (un período
    ya cerrado → usar complemento). La respuesta incluye el `periodo` usado. Sin validación
    de duplicados: se puede cargar la misma prestación más de una vez."""
    return await service.guardar_prestaciones(
        db, payload, _usuario(user), actor=service.ORIGEN_COLEGIO,
    )


@router.post(
    "/prestaciones-complementaria", response_model=GuardadoResponse,
    status_code=status.HTTP_201_CREATED,
)
async def crear_prestaciones_complementaria(
    payload: PrestacionesComplementariaCreate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Carga del **colegio** DENTRO de una factura complementaria ya abierta (creada
    con `POST /facturas/complemento`). A diferencia de `POST /prestaciones`, acá la
    cabecera ya existe de antes y se referencia por `factura_id` — no se infiere
    período ni se crea sola. 404 si la factura no existe, 422 si no es un complemento
    (`version=1`), 409 si el complemento ya está cerrado."""
    return await service.guardar_prestaciones_complementaria(
        db, payload, _usuario(user),
    )


@router.post("/medico/prestaciones", response_model=GuardadoResponse, status_code=status.HTTP_201_CREATED)
async def crear_prestaciones_medico(
    payload: PrestacionesCreate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Carga del **médico** (portal). El período sale de la fecha + ventana de la OS o
    del período activo de médicos. Requiere que la fase médico esté abierta."""
    return await service.guardar_prestaciones(
        db, payload, _usuario(user), actor=service.ORIGEN_MEDICO,
    )


@router.post("/prestaciones/mover-periodo", response_model=MoverPeriodoResponse)
async def mover_periodo(
    payload: MoverPeriodoPayload,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await service.mover_prestaciones_periodo(db, payload)


# ── Cierre de período (A → C) ────────────────────────────────────────────────
@router.get("/cierre/preview", response_model=CierrePreviewResponse)
async def cierre_preview(
    cod_obra: str = Query(...),
    periodo: str = Query(..., description="YYYYMM"),
    db: AsyncSession = Depends(get_db),
):
    return await service.preview_cierre(db, cod_obra, periodo)


@router.post("/revalorizar", response_model=revalorizar_mod.RevalorizarOut)
async def revalorizar_prestaciones(
    body: revalorizar_mod.RevalorizarIn, db: AsyncSession = Depends(get_db),
):
    """Recalcula las prestaciones ABIERTAS de un código y O.S. que se cargaron en $0
    por falta de precio, ahora que el código tiene precio. `dry_run` = vista previa
    (antes / después). Las de períodos cerrados no se tocan."""
    return await revalorizar_mod.revalorizar(db, body)


@router.post("/recalcular-precios", response_model=recotizar_mod.RecalculoOut)
async def recalcular_precios(
    body: recotizar_mod.RecalculoIn, db: AsyncSession = Depends(get_db),
):
    """Recotiza con el precio vigente las prestaciones automáticas de una factura
    ABIERTA (O.S. + período), o sólo las de un código. Fecha de la práctica (sin
    fecha, hoy) y especialidad del médico, como la carga; las manuales y las que no
    tienen precio quedan como están y se informan. `dry_run` = vista previa: no graba.
    Cerrada o liquidada → 409."""
    return await recotizar_mod.recalcular_precios(db, body)


@router.post("/cierre", response_model=CierreResponse)
async def cerrar_periodo(
    cod_obra: str = Form(...),
    periodo: str = Form(..., description="YYYYMM"),
    tipo_factura: Optional[str] = Form(None, description="Tipo de factura AFIP: A/B/C (opcional)"),
    nro_factura: Optional[str] = Form(None, description="Nro de factura AFIP (opcional)"),
    archivo: Optional[UploadFile] = File(None, description="Comprobante de la factura (opcional)"),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """multipart/form-data. `archivo` es opcional: si se envía, se guarda como
    comprobante de la factura (`documento_url` en la respuesta). `tipo_factura` y
    `nro_factura` son opcionales: texto libre (ej. "A" y "00031-00009999")."""
    return await service.cerrar_periodo(
        db, cod_obra, periodo, _usuario(user), archivo,
        nro_factura=nro_factura, tipo_factura=tipo_factura,
    )


@router.post("/facturas/complemento", response_model=FacturaRead, status_code=status.HTTP_201_CREATED)
async def abrir_complemento(
    payload: ComplementoCreate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Abre una factura complementaria (nueva versión) para un período+OS ya cerrado, para
    reenviar a la obra social prestaciones rezagadas que llegaron por excepción (la nota de
    crédito es externa al sistema). Requiere que la última versión esté cerrada (409 si hay
    una abierta; 404 si no hay factura previa). Nace como carga exclusiva del Colegio
    (fase médico cerrada)."""
    factura = await service.abrir_complemento(
        db, payload.cod_obra, payload.periodo, _usuario(user)
    )
    out = FacturaRead.model_validate(factura)
    if out.periodo:
        out.periodo_label = service.periodo_label(out.periodo)
    return out


# ── Períodos médico / colegio ────────────────────────────────────────────────
@router.get("/periodo-medico", response_model=PeriodoActivoResponse)
async def periodo_medico(cod_obra: str = Query(...), db: AsyncSession = Depends(get_db)):
    """Período abierto para carga de médicos (global con override por OS). Safety-net:
    si el puntero ya venció su `dia_corte`, se avanza acá antes de devolverlo."""
    await service.asegurar_periodo_medico_vigente(db, cod_obra)
    periodo = await service.get_periodo_medico(db, cod_obra)
    return PeriodoActivoResponse(
        cod_obra=cod_obra, periodo=periodo, periodo_label=service.periodo_label(periodo)
    )


@router.get("/periodo-colegio", response_model=PeriodoActivoResponse)
async def periodo_colegio(cod_obra: str = Query(...), db: AsyncSession = Depends(get_db)):
    """Período que el colegio está trabajando para la OS (derivado de las cabeceras).
    A diferencia de `/periodo-activo`, este SÍ puede caer sobre un complemento abierto
    (ver `version`/`es_complemento` en la respuesta) — no usar el período que devuelve
    para cargar vía `POST /prestaciones` sin revisar `es_complemento` primero."""
    periodo = await service.get_periodo_colegio(db, cod_obra)
    cabecera = await service._get_factura(db, cod_obra, periodo)
    version = cabecera.version if cabecera is not None else 1
    return PeriodoActivoResponse(
        cod_obra=cod_obra, periodo=periodo, periodo_label=service.periodo_label(periodo),
        version=version, es_complemento=version > 1,
    )


@router.get("/periodo-medico/punteros", response_model=list[PeriodoMedicoPunteroOut])
async def listar_punteros_medico(db: AsyncSession = Depends(get_db)):
    """Todos los punteros de carga de médicos: el global + uno por OS con cadencia
    propia (ej. 151 Poder Judicial que cierra a fin de mes)."""
    return await service.listar_periodos_medico(db)


@router.post("/periodo-medico/set", response_model=SetPeriodoMedicoResponse)
async def set_periodo_medico(
    payload: SetPeriodoMedicoPayload,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fija (crea o actualiza) el puntero de médicos del alcance elegido sin avanzar ni
    cerrar fases. Con `cod_obra` crea el puntero propio de esa OS (ej. fijar 151 en su
    período mientras el global avanza aparte)."""
    return await service.set_periodo_medico(db, payload.periodo, _usuario(user), payload.cod_obra)


@router.post("/cierre-doctor", response_model=CierreDoctorResponse)
async def cerrar_doctor(
    payload: CierreDoctorPayload,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cierra la fase médico de una OS+período (los médicos dejan de poder cargar)."""
    return await service.cerrar_doctor(db, payload.cod_obra, payload.periodo, _usuario(user))


@router.post("/periodo-medico/avanzar", response_model=AvanzarPeriodoMedicoResponse)
async def avanzar_periodo_medico(
    payload: AvanzarPeriodoMedicoPayload,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Avanza un mes el período de médicos (global si `cod_obra` es None; si no, el
    override de esa OS) y cierra la fase médico del período saliente."""
    return await service.avanzar_periodo_medico(db, _usuario(user), payload.cod_obra)


def _verificar_cron_secret(x_cron_secret: Optional[str] = Header(None)) -> None:
    """Auth de máquina para el endpoint que dispara el cron: header `X-Cron-Secret`
    contra `settings.CRON_SECRET`. Si no hay secreto configurado, el endpoint queda
    cerrado (falla) — nunca abierto por descuido."""
    if not settings.CRON_SECRET or x_cron_secret != settings.CRON_SECRET:
        raise HTTPException(401, "Secreto de cron inválido o no configurado")


@router.post(
    "/periodo-medico/cerrar-vencidos",
    response_model=CerrarPeriodosVencidosResponse,
    dependencies=[Depends(_verificar_cron_secret)],
)
async def cerrar_periodos_medico_vencidos(db: AsyncSession = Depends(get_db)):
    """Recorre todos los punteros de médicos y avanza los que ya vencieron su
    `dia_corte`. Idempotente — pensado para un cron diario. Auth por header
    `X-Cron-Secret` (no requiere usuario/JWT)."""
    return await service.cerrar_periodos_medico_vencidos(db)


@router.patch(
    "/prestaciones/revisado",
    response_model=list[PrestacionRead],
    response_model_by_alias=False,
)
async def marcar_revisado(
    payload: PrestacionesRevisadoUpdate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Marca y desmarca prestaciones en una sola operación de auditoría.

    Funciona en cualquier estado (abierta o cerrada). Todos los IDs deben existir;
    si alguno no existe, no se modifica ninguna prestación.
    """
    return await service.marcar_revisado(db, payload.marcados, payload.desmarcados)


@router.patch("/facturas/publicado", response_model=PublicarPeriodoResponse)
async def publicar_periodo(
    payload: PublicarPeriodoPayload,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Publica o despublica de una sola vez todas las prestaciones de una
    OS+período — único camino de la app para tocar `detalle_facturacion.publicado`.
    """
    filas = await service.publicar_periodo(
        db, cod_obra=payload.cod_obra, periodo=payload.periodo, publicado=payload.publicado,
    )
    if filas == 0:
        raise HTTPException(404, "No hay prestaciones cargadas para esa obra social y período.")
    return PublicarPeriodoResponse(
        cod_obra=payload.cod_obra, periodo=payload.periodo,
        publicado=payload.publicado, filas_actualizadas=filas,
    )


@router.get("/periodos-propios", response_model=list[PeriodoPropioOut])
async def periodos_propios(
    user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Períodos publicados del médico logueado — alimenta el selector de "Mi
    recepción". Fuerza el socio del token, sin parámetro: no es para consultar a
    otro médico."""
    return await service.listar_periodos_propios(db, str(user["nro_socio"]))


@router.get("/prestaciones/{id}", response_model=PrestacionRead, response_model_by_alias=False)
async def obtener_prestacion(id: int, db: AsyncSession = Depends(get_db)):
    """Detalle completo de una prestación — para precargar el formulario de edición
    del front. Sin restricción de estado/fase (de solo lectura)."""
    return await service.obtener_prestacion(db, id)


@router.get(
    "/prestaciones/{id}/ficha", response_model=PrestacionFichaOut,
    response_model_by_alias=False,
)
async def obtener_prestacion_ficha(id: int, db: AsyncSession = Depends(get_db)):
    """Ficha completa de una prestación: la fila cruda de `detalle_facturacion` (sin
    los recortes de `PrestacionRead`) más los bloques resueltos — médico, clínica,
    obra social, nomenclador, paciente, cabecera de factura, quién la cargó, el
    equipo quirúrgico y el historial de auditoría.

    Pantalla de consulta/soporte, **solo lectura** — no toca nada. Es una vista
    distinta de `GET /prestaciones/{id}` (que existe para precargar el formulario de
    edición): engordar aquel endpoint con estos bloques le sumaría varias queries a
    cada apertura del formulario, que se llama mucho más seguido que esta ficha.
    """
    return await service.obtener_prestacion_ficha(db, id)


@router.patch("/prestaciones/{id}", response_model=PrestacionRead, response_model_by_alias=False)
async def editar_prestacion(
    id: int,
    payload: PrestacionUpdate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await service.editar_prestacion(db, id, payload)


@router.delete("/prestaciones/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def anular_prestacion(
    id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.anular_prestacion(db, id)
