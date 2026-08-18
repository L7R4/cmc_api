"""Validación y carga de prestaciones contra obras sociales.

Montado en `/api/validaciones`. Cubre las obras sociales de **carga manual**
(Boreal 285 y Omint 243) —el prestador autoriza en el portal de la obra social y
acá registra el resultado— y Sancor (411), que se autoriza en línea.

Lo que se valida queda en `detalle_facturacion` con `origen_carga='medico'`, en
el período que devuelve `periodo_medico_actual` para esa obra social: es la
misma tabla y el mismo puntero que usa la carga del médico desde facturación,
así que entra derecho a la liquidación. Lo que respondió la obra social va en
las columnas `validacion_*` de la misma fila. El módulo no tiene tablas propias.

Nobis (62) se autoriza en línea contra el WSGeCROS de Gecros, OSPJN (151)
valida al afiliado por REST, y OSPM (433) valida contra padrón propio
(`clientes_ospm`), sin servicio externo.

Las seis obras sociales integradas están implementadas — cada una en
`app/modules/validaciones/obras/<os>/`, registrada en `obras.VALIDADORES`.
`POST /prestaciones` responde 422 si se manda cualquier otra
(`obras.obtener_o_error`).

Autorización: el prestador es el dueño del token. El personal del Colegio que
carga en nombre de un médico puede mandar `nro_socio`, pero necesita el scope
`medicos:leer`.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.auth.ownership import socio_objetivo
from app.db.database import get_db
from app.modules.validaciones import obras
from app.modules.validaciones.core import pipeline
from app.modules.validaciones.core.consultas import buscar_codigos as _buscar_codigos
from app.modules.validaciones.core.consultas import listar_periodos as _listar_periodos
from app.modules.validaciones.core.consultas import listar_prestaciones as _listar_prestaciones
from app.modules.validaciones.core.medicos import get_medico
from app.modules.validaciones.core.periodos import partes_periodo, periodo_actual, periodo_cerrado
from app.modules.validaciones.schemas import (
    CodigoOut,
    PeriodoActualOut,
    PeriodoOut,
    PrestacionCreate,
    PrestacionOut,
    PrestadorOut,
)

router = APIRouter()

def _socio_objetivo(user: dict, pedido: Optional[int]) -> int:
    """Socio sobre el que se opera.

    Por defecto el del token. Si se pide otro explícitamente, hace falta el
    scope administrativo — si no, un prestador podría leer o cargar sobre la
    matrícula de otro.

    La lógica se generalizó a `app/auth/ownership.py` y se aplica igual en
    `medicos`, `padrones`, `pagos` y `facturacion`. Se conserva este alias para
    no tocar los 9 llamadores del módulo.
    """
    return socio_objetivo(user, pedido)


@router.get("/prestador", response_model=PrestadorOut)
async def obtener_prestador(
    nro_socio: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Datos de cabecera del prestador (nombre y matrícula)."""
    socio = _socio_objetivo(user, nro_socio)
    medico = await get_medico(db, socio)
    return PrestadorOut(
        nro_socio=medico.NRO_SOCIO,
        nombre=medico.NOMBRE,
        matricula=medico.MATRICULA_PROV,
        categoria=medico.CATEGORIA or "A",
    )


@router.get("/periodo-actual", response_model=PeriodoActualOut)
async def obtener_periodo_actual(
    obra_social: int = Query(..., description="NRO_OBRASOCIAL"),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Período en el que el médico está cargando para esa O.S., y si está cerrado.

    Sale del puntero `periodo_medico_actual` (override por obra social → global),
    no del mes calendario: es el mismo período en el que caen las cargas del
    médico desde facturación.
    """
    periodo = await periodo_actual(db, obra_social)
    mes, anio = partes_periodo(periodo)
    cerrado = await periodo_cerrado(db, obra_social, periodo)
    return PeriodoActualOut(mes=mes, anio=anio, cerrado=cerrado)


@router.get("/prestaciones", response_model=List[PrestacionOut])
async def listar_prestaciones(
    obra_social: int = Query(...),
    mes: int = Query(..., ge=1, le=12),
    anio: int = Query(..., ge=2000, le=2100),
    nro_socio: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    socio = _socio_objetivo(user, nro_socio)
    return await _listar_prestaciones(db, socio, obra_social, mes, anio)


@router.get("/periodos", response_model=List[PeriodoOut])
async def listar_periodos(
    obra_social: int = Query(...),
    nro_socio: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Totales por período cargado, el más reciente primero."""
    socio = _socio_objetivo(user, nro_socio)
    return await _listar_periodos(db, socio, obra_social)


@router.get("/codigos", response_model=List[CodigoOut])
async def buscar_codigos(
    obra_social: int = Query(...),
    q: str = Query("", max_length=120),
    limit: int = Query(20, ge=1, le=50),
    nro_socio: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Códigos del nomenclador nuevo con el valor que paga esa obra social.

    El precio sale del mismo lookup que usa facturación, para que el prestador
    vea lo que efectivamente se le va a liquidar.
    """
    socio = _socio_objetivo(user, nro_socio)
    return await _buscar_codigos(db, obra_social, socio, q, limit)


@router.post("/prestaciones", response_model=PrestacionOut, status_code=status.HTTP_201_CREATED)
async def cargar_prestacion(
    payload: PrestacionCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Da de alta la prestación.

    Sancor (411) consulta el autorizador en línea; Boreal (285) y Omint (243)
    registran una autorización ya obtenida. El resto responde 422.

    `detalle_facturacion.origen_carga` queda **siempre** en `'medico'`: la
    prestación es del médico la cargue él o el Colegio en su nombre, y por eso la
    controla la fase médico del período. Quién la tipeó se distingue igual: la
    fila guarda al médico en `cod_med` y al operador del token en `usuario`.

    Despacha por `obras.POR_NRO` (ver `app/modules/validaciones/obras/`):
    agregar una obra social nueva es sumarla ahí, no tocar este endpoint.
    """
    propio = int(user["nro_socio"])
    socio = _socio_objetivo(user, payload.nro_socio)

    if payload.obra_social in obras.POR_NRO:
        return await pipeline.crear_prestacion(
            db, payload=payload, nro_socio=socio, usuario_carga=propio
        )

    obras.obtener_o_error(payload.obra_social)  # siempre levanta 422


@router.post("/prestaciones/{prestacion_id}/orden", response_model=PrestacionOut)
async def adjuntar_orden(
    prestacion_id: int,
    archivo: UploadFile = File(...),
    nro_socio: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Sube la orden/receta en PDF de una prestación (hoy lo usa Boreal)."""
    socio = _socio_objetivo(user, nro_socio)
    return await pipeline.adjuntar_orden(db, prestacion_id, socio, archivo)


@router.delete("/prestaciones/{prestacion_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_prestacion(
    prestacion_id: int,
    nro_socio: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Baja lógica: la fila queda con `validacion_anulada=1` y `estado='X'`, así
    sale de la factura sin perder la traza. En Sancor/Nobis además intenta
    anular la autorización en la obra social antes de marcar nada (ver
    `ValidadorOS.anular` de cada obra en `obras/`)."""
    socio = _socio_objetivo(user, nro_socio)
    await pipeline.eliminar_prestacion(db, prestacion_id, socio)


# Endpoints propios de una obra social (hoy: `GET /sancor/estado`,
# `POST /ospm/padron`). Cada paquete de `obras/` los declara en su propio
# `routes.py` y acá sólo se montan — así no crece un if/elif por endpoint
# especial. Validado al IMPORTAR, no en runtime: un prefijo que pise una ruta
# genérica de arriba dejaría endpoints inalcanzables sin que nadie se entere
# (los decoradores de las rutas genéricas ya corrieron cuando se llega acá, así
# que ganan los empates — mismo criterio que `app/api/routes.py`).
_RESERVADOS = frozenset(
    {"/prestaciones", "/periodos", "/periodo-actual", "/codigos", "/prestador"}
)

for _obra in obras.VALIDADORES:
    if _obra.router is None:
        continue
    if not _obra.prefijo.startswith("/") or _obra.prefijo in _RESERVADOS:
        raise RuntimeError(f"Prefijo inválido para {_obra.nombre}: {_obra.prefijo!r}")
    router.include_router(_obra.router, prefix=_obra.prefijo)
