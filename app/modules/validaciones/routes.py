"""Validación y carga de prestaciones contra obras sociales.

Montado en `/api/validaciones`. Cubre las obras sociales de **carga manual**
(Boreal 285 y Omint 243) —el prestador autoriza en el portal de la obra social y
acá registra el resultado— y Sancor (411), que se autoriza en línea.

Lo que se valida queda en `detalle_facturacion` con `origen_carga='medico'`, en
el período que devuelve `periodo_medico_actual` para esa obra social: es la
misma tabla y el mismo puntero que usa la carga del médico desde facturación,
así que entra derecho a la liquidación. Lo que respondió la obra social va en
las columnas `validacion_*` de la misma fila. El módulo no tiene tablas propias.

Nobis (402) se autoriza en línea contra el WSGeCROS de Gecros, OSPJN (151)
valida al afiliado por REST, y OSPM (433) valida contra padrón propio
(`clientes_ospm`), sin servicio externo.

Las seis obras sociales integradas están implementadas. `POST /prestaciones`
responde 422 si se manda cualquier otra.

Autorización: el prestador es el dueño del token. El personal del Colegio que
carga en nombre de un médico puede mandar `nro_socio`, pero necesita el scope
`medicos:leer`.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.auth.ownership import socio_objetivo
from app.core.config import settings
from app.db.database import get_db
from app.modules.validaciones import sancor, service
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


@router.get("/sancor/estado")
async def estado_sancor(user=Depends(get_current_user)):
    """Contra qué autorizador de Sancor está apuntando el backend.

    `simulado` no manda nada a Sancor: sirve para probar la pantalla completa.
    `test`/`produccion` sí generan autorizaciones reales — `produccion` consume
    el token del afiliado de verdad.
    """
    modo = sancor.modo_actual()
    destino = {
        sancor.MODO_SIMULADO: "no se envía nada (respuesta armada localmente)",
        sancor.MODO_TEST: settings.SANCOR_URL_TEST,
        sancor.MODO_PRODUCCION: settings.SANCOR_URL_PROD,
    }.get(modo, "modo desconocido — se trata como test")
    return {
        "modo": modo,
        "destino": destino,
        "genera_autorizaciones_reales": modo in (sancor.MODO_TEST, sancor.MODO_PRODUCCION),
    }


@router.get("/prestador", response_model=PrestadorOut)
async def obtener_prestador(
    nro_socio: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Datos de cabecera del prestador (nombre y matrícula)."""
    socio = _socio_objetivo(user, nro_socio)
    medico = await service.get_medico(db, socio)
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
    periodo = await service.periodo_actual(db, obra_social)
    mes, anio = service.partes_periodo(periodo)
    cerrado = await service.periodo_cerrado(db, obra_social, periodo)
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
    return await service.listar_prestaciones(db, socio, obra_social, mes, anio)


@router.get("/periodos", response_model=List[PeriodoOut])
async def listar_periodos(
    obra_social: int = Query(...),
    nro_socio: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Totales por período cargado, el más reciente primero."""
    socio = _socio_objetivo(user, nro_socio)
    return await service.listar_periodos(db, socio, obra_social)


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
    return await service.buscar_codigos(db, obra_social, socio, q, limit)


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
    """
    propio = int(user["nro_socio"])
    socio = _socio_objetivo(user, payload.nro_socio)

    if payload.obra_social == service.SANCOR_OS:
        return await service.validar_sancor(
            db,
            nro_socio=socio,
            codigo=payload.codigo.strip(),
            nro_afiliado=payload.nro_afiliado,
            barra_afiliado=payload.barra_afiliado,
            token=payload.token,
            cantidad=payload.cantidad,
            usuario_carga=propio,
        )

    if payload.obra_social == service.OSPJN_OS:
        return await service.validar_ospjn(
            db,
            nro_socio=socio,
            codigo=payload.codigo.strip(),
            nro_afiliado=payload.nro_afiliado,
            barra_afiliado=payload.barra_afiliado,
            cantidad=payload.cantidad,
            usuario_carga=propio,
        )

    if payload.obra_social == service.NOBIS_OS:
        return await service.validar_nobis(
            db,
            nro_socio=socio,
            codigo=payload.codigo.strip(),
            nro_afiliado=payload.nro_afiliado,
            token=payload.token,
            cantidad=payload.cantidad,
            usuario_carga=propio,
        )

    if payload.obra_social == service.OSPM_OS:
        # OSPM no consulta a nadie: valida contra su padrón propio. El DNI viaja
        # en `nro_afiliado`, que es el campo que manda el formulario.
        return await service.validar_ospm(
            db,
            nro_socio=socio,
            codigo=payload.codigo.strip(),
            documento=payload.nro_afiliado,
            cantidad=payload.cantidad,
            usuario_carga=propio,
        )

    obra = service.obra_manual_o_error(payload.obra_social)
    return await service.crear_prestacion_manual(
        db,
        nro_socio=socio,
        obra=obra,
        codigo=payload.codigo.strip(),
        nombre_afiliado=payload.nombre_afiliado,
        nro_afiliado=payload.nro_afiliado,
        nro_autorizacion=payload.nro_validacion,
        coseguro=payload.coseguro,
        cantidad=payload.cantidad,
        usuario_carga=propio,
    )


@router.post("/ospm/padron")
async def importar_padron_ospm(
    archivo: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Reemplaza el padrón de OSPM con el CSV/TXT que manda la obra social.

    Formato: `AFILIADO, DU, CUIT, ACTIVO`, separador `;` o `,`, con encabezado
    opcional. Es una operación del Colegio, no del prestador: pide el scope
    administrativo.

    Reemplaza el contenido de `clientes_ospm`, el mismo padrón que carga
    `importar_padron_ospm.php`: el padrón es uno solo, así que el legacy y la API
    validan siempre contra el mismo dato.
    """
    if SCOPE_ADMIN not in (user.get("scopes") or []):
        raise HTTPException(403, "Sólo el Colegio puede importar el padrón de OSPM.")
    return await service.importar_padron_ospm(db, archivo)


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
    return await service.adjuntar_orden(db, prestacion_id, socio, archivo)


@router.delete("/prestaciones/{prestacion_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_prestacion(
    prestacion_id: int,
    nro_socio: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Baja lógica: la fila queda con `validacion_anulada=1` y `estado='X'`, así
    sale de la factura sin perder la traza. En Sancor además anula la
    autorización en la obra social antes de marcar nada (ver el service)."""
    socio = _socio_objetivo(user, nro_socio)
    await service.eliminar_prestacion(db, prestacion_id, socio)
