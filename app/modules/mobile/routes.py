"""
MOBILE APP (cmc-app) ONLY — Backend-For-Frontend for the doctor-facing mobile app.

Mounted at /api/mobile, tagged "Mobile". Read-only over the existing domain
data: these endpoints only SELECT and reuse the existing services/models, and
never mutate a médico, a liquidación or a valor. The two exceptions are the
app's own inboxes — /auth/* (session) and POST /solicitudes-cambio, which
appends a new row to its own table and touches nothing else. The website does
NOT use these routes; it keeps its own module endpoints untouched.

Values come exclusively from the nm_ tables (NomencladorCMC / HistorialPrecioCodigo /
Valor / Galeno) via the existing reportes_nm logic — never from valor_prestacion.

To add a mobile feature: add an endpoint here (reusing existing services), add its
slim schema in ./schemas.py, and wire the matching repo in the app's src/api/repos.
"""
import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from jose import ExpiredSignatureError, JWTError
from pydantic import BaseModel
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import sessions
from app.auth.deps import get_current_user_with_scopes_and_role, get_user_role
from app.auth.permissions import get_effective_permission_codes
from app.core import ratelimit
from app.core.passwords import verify_and_upgrade
from app.core.security import create_access_token, decode_token
from app.db.database import get_db
from app.db.models import Especialidad, ListadoMedico
from app.db.models.catalogs import ObrasSociales
from app.db.models.legacy import MedicoObraSocial
from app.db.models.nomenclador_cmc import (
    Galeno,
    HistorialPrecioCodigo,
    MedicoCodigoHabilitado,
    NomencladorCMC,
    NomencladorEspecialidad,
)
from app.modules.nomenclador.routes_reportes import tabla_valores as _tabla_valores
from app.modules.nomenclador.service import _especialidades_medico
from app.modules.nomenclador.schemas import TablaValoresItem
from app.modules.avisos import service as avisos_service
from app.modules.beneficios import service as beneficios_service
from app.modules.dispositivos import service as dispositivos_service
from app.modules.solicitudes_cambio import service as solicitudes_cambio_service
from app.modules.mobile.schemas import (
    AvisoItem,
    BeneficioItem,
    ConsultaComunItem,
    CredencialItem,
    EspecialidadMedicoItem,
    GalenoBoletinItem,
    GalenoValorItem,
    MedicoProfileItem,
    NomencladorItem,
    ObraSocialItem,
    DispositivoIn,
    DispositivoOut,
    PadronItem,
    SolicitudCambioIn,
    SolicitudCambioOutMobile,
)

# Código del valor que publica el boletín de consulta. Es el MISMO que usa el
# panel web (BoletinConsultaComun/boletinConsultaComun.constants.ts), que es el
# que genera el boletín oficial — cambiar uno sin el otro los desincroniza.
#
# OJO con la descripción del nomenclador, es contraintuitiva:
#   420101 "CONSULTA COMUN"         → sólo 10 obras sociales con valor vigente
#   420351 "CONSULTA ESPECIALIZADA" → 34 obras sociales; es el que se publica
CONSULTA_COMUN_CODE = "420351"

router = APIRouter()


# ── Perfil / credencial helpers ───────────────────────────────────────────────

# NRO_ESPECIALIDAD* guardan ID_COLEGIO_ESPE en orden de prioridad (principal = el
# primero, sin sufijo). Mismo criterio que auth._especialidades_ordenadas.
_ESP_FIELDS = (
    "NRO_ESPECIALIDAD", "NRO_ESPECIALIDAD2", "NRO_ESPECIALIDAD3",
    "NRO_ESPECIALIDAD4", "NRO_ESPECIALIDAD5", "NRO_ESPECIALIDAD6",
)

# Placeholders del esquema legacy (server_default) que representan "vacío".
_PLACEHOLDERS = {"", "a", "A", "0"}


def _clean(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return None if s in _PLACEHOLDERS else s


def _to_int(v) -> Optional[int]:
    s = _clean(v)
    if not s:
        return None
    try:
        n = int(float(s))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _nombre_apellido(user) -> tuple[str, Optional[str]]:
    """(nombre, apellido). Usa las columnas nuevas apellido/nombre_ si existen;
    si no, parsea el legacy NOMBRE con formato 'APELLIDO, Nombre'."""
    apellido = _clean(getattr(user, "apellido", None))
    nombre = _clean(getattr(user, "nombre_", None))
    if apellido or nombre:
        return (nombre or "", apellido)
    full = _clean(getattr(user, "NOMBRE", None)) or ""
    if "," in full:
        ap, no = full.split(",", 1)
        return (no.strip(), ap.strip() or None)
    return (full, None)


async def _especialidades(user, db: AsyncSession) -> List[EspecialidadMedicoItem]:
    esp_ids = [int(getattr(user, f)) for f in _ESP_FIELDS if getattr(user, f, 0)]
    if not esp_ids:
        return []
    nombres: dict[int, str] = {}
    rows = await db.execute(
        select(Especialidad.ID_COLEGIO_ESPE, Especialidad.ESPECIALIDAD)
        .where(Especialidad.ID_COLEGIO_ESPE.in_(esp_ids))
    )
    for id_colegio, nombre in rows.all():
        nombres.setdefault(int(id_colegio), str(nombre))
    # Solo especialidades con nombre en el catálogo; las que no resuelven se omiten
    # (nunca mostrar "Especialidad {nro}"). La principal es la primera que resuelve.
    out: List[EspecialidadMedicoItem] = []
    for e in esp_ids:
        nombre = nombres.get(e)
        if not nombre:
            continue
        out.append(EspecialidadMedicoItem(id_colegio=e, nombre=nombre, principal=(len(out) == 0)))
    return out


# ── Precios: catálogo + búsqueda + valores (nm_ tables) ──────────────────────

@router.get("/obras-sociales", response_model=List[ObraSocialItem])
async def obras_sociales(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user_with_scopes_and_role),
):
    """Todas las obras sociales (nro + nombre), ordenadas — para el picker de Precios.

    Selecciona solo las columnas legacy (NRO_OBRASOCIAL, OBRA_SOCIAL) en vez de la
    entidad completa: así no depende de columnas agregadas por migraciones que el
    padrón importado puede no tener."""
    rows = (
        await db.execute(
            select(ObrasSociales.NRO_OBRASOCIAL, ObrasSociales.OBRA_SOCIAL)
            .where(ObrasSociales.NRO_OBRASOCIAL.is_not(None))
            .order_by(ObrasSociales.OBRA_SOCIAL.asc())
        )
    ).all()
    return [
        ObraSocialItem(nro_obra_social=nro, nombre=nombre or str(nro))
        for nro, nombre in rows
    ]


@router.get("/nomenclador", response_model=List[NomencladorItem])
async def nomenclador(
    q: str = Query(..., min_length=2, description="Código o descripción a buscar"),
    db: AsyncSession = Depends(get_db),
    dep=Depends(get_current_user_with_scopes_and_role),
):
    """Búsqueda en el nomenclador CMC (solo activos), FILTRADA a los códigos del médico
    logueado: los de SUS especialidades (nm_nomenclador_especialidad) + los universales
    (sin_restriccion_especialidad) + habilitaciones individuales, menos inhabilitaciones.
    Un cardiólogo no ve códigos exclusivos de otra especialidad. Misma regla que el gate
    de facturación de la web. Los médicos con varias especialidades ven la unión."""
    user, _scopes, _role = dep
    term = q.strip()
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
            & (MedicoCodigoHabilitado.activo == True)  # noqa: E712
            & vigencia_ok
        )

    esp_habilitada = exists().where(
        (NomencladorEspecialidad.nomenclador_id == NomencladorCMC.id)
        & (NomencladorEspecialidad.especialidad_id_colegio.in_(especialidades))
        & (NomencladorEspecialidad.activo == True)  # noqa: E712
    )

    stmt = (
        select(NomencladorCMC)
        .where(
            NomencladorCMC.activo == True,  # noqa: E712
            NomencladorCMC.codigo.contains(term)
            | NomencladorCMC.descripcion.contains(term),
            ~_override_vigente("inhabilita"),
            or_(
                _override_vigente("habilita"),
                NomencladorCMC.sin_restriccion_especialidad == True,  # noqa: E712
                esp_habilitada,
            ),
        )
        .order_by(NomencladorCMC.codigo)
        .limit(15)
    )
    return (await db.execute(stmt)).scalars().all()


@router.get("/tabla-valores", response_model=List[TablaValoresItem])
async def tabla_valores(
    obra_social_nro: int = Query(...),
    codigo: Optional[str] = Query(None),
    especialidades: Optional[List[int]] = Query(
        None,
        description="ID_COLEGIO_ESPE del médico en orden de prioridad (principal primero).",
    ),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user_with_scopes_and_role),
):
    """Valor vigente de una práctica para una OS, resuelto desde las tablas nm_.
    Reutiliza íntegra la lógica de selección de variante (NE > NNE > NN + especialidad)
    de reportes_nm.tabla_valores; acá solo se fija el tamaño de página al uso del app."""
    return await _tabla_valores(
        obra_social_nro=obra_social_nro,
        fecha=None,
        codigo=codigo,
        especialidades=especialidades,
        orden="codigo",
        page=1,
        size=5,
        db=db,
    )


# ── Perfil / credencial del médico logueado ──────────────────────────────────

@router.get("/perfil", response_model=MedicoProfileItem)
async def perfil(
    db: AsyncSession = Depends(get_db),
    dep=Depends(get_current_user_with_scopes_and_role),
):
    """Perfil view-only del médico autenticado, armado desde su ListadoMedico."""
    user, _scopes, _role = dep
    nombre, apellido = _nombre_apellido(user)
    return MedicoProfileItem(
        id=user.ID,
        nro_socio=user.NRO_SOCIO,
        nombre=nombre,
        apellido=apellido,
        documento=_to_int(getattr(user, "DOCUMENTO", None)),
        tipo_doc=_clean(getattr(user, "TIPO_DOC", None)),
        cuit=_clean(getattr(user, "CUIT", None)),
        matricula_prov=(str(user.MATRICULA_PROV) if _to_int(user.MATRICULA_PROV) else None),
        matricula_nac=(str(user.MATRICULA_NAC) if _to_int(user.MATRICULA_NAC) else None),
        email=_clean(getattr(user, "MAIL_PARTICULAR", None)),
        telefono=(
            _clean(getattr(user, "TELEFONO_CONSULTA", None))
            or _clean(getattr(user, "TELE_PARTICULAR", None))
        ),
        celular=_clean(getattr(user, "CELULAR_PARTICULAR", None)),
        domicilio=(
            _clean(getattr(user, "DOMICILIO_CONSULTA", None))
            or _clean(getattr(user, "DOMICILIO_PARTICULAR", None))
        ),
        localidad=_clean(getattr(user, "localidad", None)),
        provincia=_clean(getattr(user, "PROVINCIA", None)),
        codigo_postal=_clean(getattr(user, "CODIGO_POSTAL", None)),
        sexo=(user.SEXO if getattr(user, "SEXO", None) in ("M", "F") else None),
        fecha_nac=getattr(user, "FECHA_NAC", None),
        existe=getattr(user, "EXISTE", "N") or "N",
        categoria=(str(getattr(user, "CATEGORIA", "") or "").strip() or None),
        condicion_impositiva=_clean(getattr(user, "condicion_impositiva", None)),
        monotributista=(
            str(getattr(user, "MONOTRIBUTISTA", "") or "").strip().upper() in ("SI", "S", "SÍ")
        ),
        vitalicio=(getattr(user, "VITALICIO", "N") == "S"),
        fecha_ingreso=getattr(user, "FECHA_INGRESO", None),
        fecha_matricula=getattr(user, "FECHA_MATRICULA", None),
        especialidades=await _especialidades(user, db),
        vencimiento_anssal=getattr(user, "VENCIMIENTO_ANSSAL", None),
        vencimiento_malapraxis=getattr(user, "VENCIMIENTO_MALAPRAXIS", None),
        vencimiento_cobertura=getattr(user, "VENCIMIENTO_COBERTURA", None),
    )


@router.get("/padrones", response_model=List[PadronItem])
async def padrones(
    db: AsyncSession = Depends(get_db),
    dep=Depends(get_current_user_with_scopes_and_role),
):
    """Obras sociales en cuyo padrón está inscripto el médico logueado (solo lectura).
    Se deduplica por OS, juntando las especialidades bajo las que figura."""
    user, _scopes, _role = dep
    rows = (
        await db.execute(
            select(
                MedicoObraSocial.NRO_OBRASOCIAL,
                MedicoObraSocial.CATEGORIA,
                MedicoObraSocial.ESPECIALIDAD,
            ).where(MedicoObraSocial.NRO_SOCIO == user.NRO_SOCIO)
        )
    ).all()
    if not rows:
        return []

    por_os: dict[int, dict] = {}
    numeric_codes: set[int] = set()
    for nro, cat, esp in rows:
        info = por_os.setdefault(nro, {"categoria": None, "esp": set()})
        c = (cat or "").strip()
        if c and c.upper() != "A" and info["categoria"] is None:
            info["categoria"] = c
        e = (esp or "").strip()
        if e and e.upper() != "A":  # 'A' es placeholder legacy
            info["esp"].add(e)
            if e.isdigit():
                numeric_codes.add(int(e))

    # Algunos ESPECIALIDAD vienen como código (ID_COLEGIO_ESPE) en vez de nombre:
    # se resuelven a nombre y los que no resuelven se descartan (no mostrar números).
    code_names: dict[int, str] = {}
    if numeric_codes:
        erows = await db.execute(
            select(Especialidad.ID_COLEGIO_ESPE, Especialidad.ESPECIALIDAD)
            .where(Especialidad.ID_COLEGIO_ESPE.in_(numeric_codes))
        )
        for idc, nom in erows.all():
            code_names[int(idc)] = str(nom).strip()

    def _esp_names(raw: set[str]) -> list[str]:
        names: set[str] = set()
        for e in raw:
            if e.isdigit():
                nm = code_names.get(int(e))
                if nm:
                    names.add(nm)
            else:
                names.add(e)
        return sorted(names)

    nombres: dict[int, str] = {}
    nrows = await db.execute(
        select(ObrasSociales.NRO_OBRASOCIAL, ObrasSociales.OBRA_SOCIAL)
        .where(ObrasSociales.NRO_OBRASOCIAL.in_(list(por_os.keys())))
    )
    for nro, nombre in nrows.all():
        nombres[nro] = nombre or str(nro)

    out = [
        PadronItem(
            nro_obra_social=nro,
            nombre=nombres.get(nro, str(nro)),
            categoria=info["categoria"],
            especialidades=_esp_names(info["esp"]),
        )
        for nro, info in por_os.items()
    ]
    out.sort(key=lambda x: x.nombre)
    return out


@router.get("/credencial", response_model=CredencialItem)
async def credencial(
    db: AsyncSession = Depends(get_db),
    dep=Depends(get_current_user_with_scopes_and_role),
):
    """Credencial digital derivada del médico autenticado."""
    user, _scopes, _role = dep
    nombre, apellido = _nombre_apellido(user)
    especialidades = await _especialidades(user, db)
    display_name = f"{apellido.upper()}, {nombre}" if apellido else nombre
    return CredencialItem(
        nro_socio=user.NRO_SOCIO,
        display_name=display_name,
        titulo=("Dra." if getattr(user, "SEXO", None) == "F" else "Dr."),
        documento=_to_int(getattr(user, "DOCUMENTO", None)),
        especialidad_principal=(especialidades[0].nombre if especialidades else None),
        activo=(getattr(user, "EXISTE", "N") == "S"),
        emitida=datetime.date.today(),
    )


# ── Boletín de valores: consulta común (nm_ tables) ──────────────────────────

@router.get("/boletin/consulta", response_model=List[ConsultaComunItem])
async def boletin_consulta(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user_with_scopes_and_role),
):
    """Valor de la consulta del boletín (código 420351) por obra social: variante general
    (sin especialidad), vigente hoy, una fila por OS. Nombres de OS por columnas
    legacy para no depender de columnas agregadas por migraciones."""
    fecha = datetime.date.today()
    nom_id = (
        await db.execute(
            select(NomencladorCMC.id).where(NomencladorCMC.codigo == CONSULTA_COMUN_CODE)
        )
    ).scalar_one_or_none()
    if nom_id is None:
        return []

    rows = (
        await db.execute(
            select(
                HistorialPrecioCodigo.obra_social_nro,
                HistorialPrecioCodigo.precio_total,
            )
            .where(
                HistorialPrecioCodigo.nomenclador_id == nom_id,
                HistorialPrecioCodigo.especialidad_id_colegio.is_(None),
                HistorialPrecioCodigo.vigencia_desde <= fecha,
                (HistorialPrecioCodigo.vigencia_hasta.is_(None))
                | (HistorialPrecioCodigo.vigencia_hasta >= fecha),
            )
            .order_by(HistorialPrecioCodigo.precio_total.desc())
        )
    ).all()

    por_os: dict[int, str] = {}
    for nro, precio in rows:
        por_os.setdefault(nro, str(precio))
    if not por_os:
        return []

    nombres: dict[int, str] = {}
    nrows = await db.execute(
        select(ObrasSociales.NRO_OBRASOCIAL, ObrasSociales.OBRA_SOCIAL)
        .where(ObrasSociales.NRO_OBRASOCIAL.in_(list(por_os.keys())))
    )
    for nro, nombre in nrows.all():
        nombres[nro] = nombre or str(nro)

    return [
        ConsultaComunItem(
            nro_obra_social=nro,
            nombre=nombres.get(nro, str(nro)),
            valor=valor,
            fecha_cambio=None,
        )
        for nro, valor in sorted(por_os.items(), key=lambda kv: nombres.get(kv[0], ""))
    ]


@router.get("/boletin/galenos", response_model=List[GalenoBoletinItem])
async def boletin_galenos(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user_with_scopes_and_role),
):
    """Galenos vigentes por obra social, agrupados en galenos/gastos. Cada código se
    colapsa a un valor (rango sólo si los niveles difieren); se omiten los no cargados
    (valor 0). No se expone el nivel (no interesa al usuario)."""
    rows = (
        await db.execute(
            select(
                Galeno.obra_social_nro,
                Galeno.codigo,
                Galeno.nombre,
                Galeno.valor_unitario,
            ).where(Galeno.vigencia_hasta.is_(None), Galeno.activo == True)  # noqa: E712
        )
    ).all()

    # Colapsar por (OS, código): min/max del valor entre niveles vigentes.
    from decimal import Decimal

    agg: dict[tuple[int, str], dict] = {}
    for os_nro, codigo, nombre, valor in rows:
        v = valor if isinstance(valor, Decimal) else Decimal(str(valor or 0))
        cur = agg.get((os_nro, codigo))
        if cur is None:
            agg[(os_nro, codigo)] = {"nombre": nombre, "min": v, "max": v}
        else:
            cur["min"] = min(cur["min"], v)
            cur["max"] = max(cur["max"], v)

    por_os: dict[int, dict[str, list]] = {}
    for (os_nro, codigo), info in agg.items():
        if info["max"] <= 0:  # galeno sin valor cargado → no se muestra
            continue
        item = GalenoValorItem(
            codigo=codigo,
            nombre=info["nombre"],
            valor=str(info["min"]),
            valor_hasta=(str(info["max"]) if info["max"] != info["min"] else None),
        )
        grupo = "gastos" if codigo.startswith("gasto") else "galenos"
        por_os.setdefault(os_nro, {"galenos": [], "gastos": []})[grupo].append(item)

    if not por_os:
        return []

    nombres: dict[int, str] = {}
    nrows = await db.execute(
        select(ObrasSociales.NRO_OBRASOCIAL, ObrasSociales.OBRA_SOCIAL)
        .where(ObrasSociales.NRO_OBRASOCIAL.in_(list(por_os.keys())))
    )
    for nro, nombre in nrows.all():
        nombres[nro] = nombre or str(nro)

    out = [
        GalenoBoletinItem(
            nro_obra_social=os_nro,
            nombre=nombres.get(os_nro, str(os_nro)),
            galenos=sorted(g["galenos"], key=lambda x: x.nombre),
            gastos=sorted(g["gastos"], key=lambda x: x.nombre),
        )
        for os_nro, g in por_os.items()
    ]
    out.sort(key=lambda x: x.nombre)
    return out


# ── Beneficios / convenios para socios ───────────────────────────────────────

@router.get("/beneficios", response_model=List[BeneficioItem])
async def beneficios(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user_with_scopes_and_role),
):
    """Beneficios ACTIVOS, ordenados por categoría y título. Los da de alta el
    panel web (scope beneficios:gestionar); acá es sólo lectura.

    Un beneficio vencido (vigencia_hasta pasada) se sigue devolviendo mientras
    esté activo: `vigencia_hasta` viaja en la respuesta y decide el app. Para
    sacarlo de la lista, el admin lo desactiva."""
    return await beneficios_service.listar_activos(db)


# ── Avisos del Colegio ───────────────────────────────────────────────────────

@router.get("/avisos", response_model=List[AvisoItem])
async def avisos(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user_with_scopes_and_role),
):
    """Avisos ACTIVOS, el más reciente primero (tope 50). Los publica el panel
    web (scope avisos:gestionar); acá es sólo lectura.

    Esto es la bandeja que el app consulta (pull). Es independiente de la
    notificación push que despierta el teléfono: ese despacho todavía no está
    implementado, así que hoy el socio ve el aviso al abrir el app. Un aviso que
    el admin baja (activo=false) desaparece de esta lista."""
    return await avisos_service.listar_activos(db)


# ── Reclamos de corrección de datos ("Reclamar cambios") ─────────────────────

@router.post(
    "/solicitudes-cambio",
    response_model=SolicitudCambioOutMobile,
    status_code=201,
)
async def crear_solicitud_cambio(
    body: SolicitudCambioIn,
    db: AsyncSession = Depends(get_db),
    dep=Depends(get_current_user_with_scopes_and_role),
):
    """El médico reporta que un dato suyo está mal. Queda 'pendiente' para que
    el admin la resuelva desde el panel.

    nro_socio y medico_id salen del token — el body no puede elegirlos. Hay un
    tope de pendientes por médico (429) para no inundar la bandeja."""
    user, _scopes, _role = dep
    obj = await solicitudes_cambio_service.crear_desde_movil(
        db,
        nro_socio=user.NRO_SOCIO,
        medico_id=user.ID,
        campo=body.campo,
        valor_actual=body.valor_actual,
        valor_propuesto=body.valor_propuesto,
        mensaje=body.mensaje,
    )
    await db.commit()
    await db.refresh(obj)
    return SolicitudCambioOutMobile(
        id=obj.id, campo=obj.campo, estado=obj.estado, created_at=obj.created_at
    )


# ── Dispositivos para notificaciones push ────────────────────────────────────
# El app registra su Expo push token al iniciar sesión y lo da de baja al
# cerrarla. Tabla propia (dispositivos_push): no toca nada del dominio.

@router.post("/dispositivos", response_model=DispositivoOut, status_code=201)
async def registrar_dispositivo(
    body: DispositivoIn,
    db: AsyncSession = Depends(get_db),
    dep=Depends(get_current_user_with_scopes_and_role),
):
    """Alta o refresh del token de push de ESTE dispositivo.

    El dueño sale del token de sesión: el body no puede elegir a qué médico
    asociarlo. Si el token ya existía se reasigna al socio actual (teléfono que
    cambia de dueño), de modo que el anterior deja de recibir sus avisos.

    Un token con formato inválido se rechaza acá y no llega a la tabla: mandarlo
    al proveedor sería un request desperdiciado en cada aviso.
    """
    user, _scopes, _role = dep

    if not dispositivos_service.token_valido(body.expo_push_token):
        raise HTTPException(status_code=422, detail="Token de push inválido.")

    await dispositivos_service.registrar(
        db,
        medico_id=user.ID,
        token=body.expo_push_token,
        plataforma=body.plataforma,
    )
    await db.commit()
    return DispositivoOut(registrado=True)


@router.delete("/dispositivos", response_model=DispositivoOut)
async def baja_dispositivo(
    body: DispositivoIn,
    db: AsyncSession = Depends(get_db),
    dep=Depends(get_current_user_with_scopes_and_role),
):
    """Baja del token en el logout, para que el próximo usuario del teléfono no
    reciba los avisos del anterior.

    Acotado al médico del token de sesión: mandar un token ajeno no hace nada,
    así nadie puede apagarle las notificaciones a otro socio.
    """
    user, _scopes, _role = dep
    await dispositivos_service.baja(db, medico_id=user.ID, token=body.expo_push_token)
    await db.commit()
    return DispositivoOut(registrado=False)


# ── Auth para el app móvil (tokens en el body, sin cookies) ──────────────────
# El flujo web de /auth/* usa cookie HttpOnly + CSRF double-submit, que React
# Native no puede leer. Acá el refresh token viaja en el body y el app lo guarda
# en SecureStore; así el mismo flujo sirve en web y en nativo. No toca /auth/*.

class MobileLoginIn(BaseModel):
    nro_socio: int
    password: str


class MobileRefreshIn(BaseModel):
    refresh_token: str


async def _issue_session(
    medico: ListadoMedico,
    db: AsyncSession,
    *,
    refresh: str,
) -> dict:
    """Arma el response de sesión. El refresh ya viene emitido y registrado.

    Se recibe hecho en vez de crearlo acá porque el login y el refresh lo
    obtienen distinto: el primero abre una sesión nueva, el segundo **rota** una
    existente y tiene que revocar la anterior en la misma operación. Ver
    app/auth/sessions.py.
    """
    scopes = await get_effective_permission_codes(db, medico.ID)
    role = await get_user_role(db, medico.ID)
    access = create_access_token(
        sub=str(medico.NRO_SOCIO), uid=medico.ID, scopes=scopes, role=role
    )
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "user": {
            "id": medico.ID,
            "nro_socio": medico.NRO_SOCIO,
            "nombre": getattr(medico, "NOMBRE", None),
            "scopes": scopes,
            "role": role,
            "ingresar": getattr(medico, "INGRESAR", None),
            "especialidades": _especialidades_medico(medico),
            "es_organizacion": getattr(medico, "es_organizacion", False),
            # El app manda a la pantalla de cambio de contraseña antes de
            # mostrar nada: la cuenta todavía tiene la clave inicial pública.
            "must_change_password": bool(getattr(medico, "must_change_password", False)),
        },
    }


@router.post("/auth/login")
async def mobile_login(
    body: MobileLoginIn, req: Request, db: AsyncSession = Depends(get_db)
):
    """Login del app: valida credenciales y devuelve access + refresh en el body."""
    # Mismo contador que /auth/login: comparten las claves `ip:` y `socio:`, así
    # que agotar los intentos en un flujo también los agota en el otro. Limitar
    # solo el login web dejaría esta puerta abierta con el mismo espacio de
    # contraseñas.
    ratelimit.chequear_login(req, body.nro_socio)

    medico = (
        await db.execute(select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == body.nro_socio))
    ).scalar_one_or_none()
    if not medico:
        ratelimit.registrar_fallo(req, body.nro_socio)
        raise HTTPException(401, "Usuario inválido")
    if getattr(medico, "EXISTE", "N") != "S":
        raise HTTPException(403, "Tu registro está pendiente de aprobación")
    if not await verify_and_upgrade(db, medico, body.password):
        ratelimit.registrar_fallo(req, body.nro_socio)
        raise HTTPException(401, "Credenciales inválidas")

    ratelimit.registrar_exito(req, body.nro_socio)
    refresh = await sessions.emitir_refresh(db, medico, origen="mobile", request=req)
    return await _issue_session(medico, db, refresh=refresh)


@router.post("/auth/refresh")
async def mobile_refresh(
    body: MobileRefreshIn, req: Request, db: AsyncSession = Depends(get_db)
):
    """Canjea un refresh token (del SecureStore del app) por un access nuevo.

    Rota: el token que se presenta queda revocado y el app se queda con el
    nuevo. Esto importa más en móvil que en web — el refresh vive en el
    dispositivo, no en una cookie HttpOnly, y es lo que se lleva quien saca una
    copia del almacenamiento del teléfono. Con rotación, la próxima vez que
    cualquiera de los dos use el token viejo se detecta el reuso y se cierran
    todas las sesiones del socio.
    """
    try:
        payload = decode_token(body.refresh_token)
    except ExpiredSignatureError:
        raise HTTPException(401, "refresh_expired")
    except JWTError:
        raise HTTPException(401, "refresh_invalid")
    if payload.get("type") != "refresh":
        raise HTTPException(401, "not_a_refresh_token")
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(401, "refresh sin sub")
    medico = (
        await db.execute(select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == int(sub)))
    ).scalar_one_or_none()
    if not medico:
        raise HTTPException(401, "Usuario no encontrado")

    try:
        refresh = await sessions.rotar_refresh(
            db, medico, payload.get("jti"), origen="mobile", request=req
        )
    except sessions.SesionInvalida as e:
        raise HTTPException(401, e.codigo)

    return await _issue_session(medico, db, refresh=refresh)


@router.post("/auth/logout")
async def mobile_logout(
    body: MobileRefreshIn,
    db: AsyncSession = Depends(get_db),
    dep=Depends(get_current_user_with_scopes_and_role),
):
    """Cierra la sesión del dispositivo.

    No existía: el app borraba el token del SecureStore y listo, con lo que un
    refresh copiado del teléfono seguía sirviendo 15 días después del logout.

    Se exige access token además del refresh para que nadie pueda desloguear a
    otro socio con un refresh que encontró; y se verifica que el refresh sea del
    mismo usuario, para que un token válido no sirva para cerrar sesiones ajenas.
    """
    user, _scopes, _role = dep
    try:
        payload = decode_token(body.refresh_token)
    except Exception:
        # Vencido o ilegible: no hay nada que revocar y el app ya lo va a
        # descartar. Responder 200 evita que el logout falle por un token que ya
        # no sirve.
        return {"ok": True}

    if str(payload.get("sub")) != str(user.NRO_SOCIO):
        raise HTTPException(403, "El refresh no pertenece a esta sesión")

    await sessions.revocar_por_jti(db, payload.get("jti"), motivo="logout")
    return {"ok": True}
