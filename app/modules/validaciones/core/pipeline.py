"""Orquesta el alta y la baja de una prestación contra la obra social
correspondiente. Es el único lugar de `core/` que conoce el registro de
obras sociales (`obras.POR_NRO`) — nada más acá sabe que existen obras
sociales particulares.
"""
import datetime
import os
import uuid

from fastapi import HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.files import UPLOAD_ROOT
from app.common.uploads import validate_upload
from app.db.models import DetalleFacturacionCMC
from app.modules.facturacion.service import _cleanup_factura_si_vacia
from app.modules.validaciones import obras
from app.modules.validaciones.core.consultas import _descripciones
from app.modules.validaciones.core.contrato import Contexto
from app.modules.validaciones.core.entrada import parsear_entrada
from app.modules.validaciones.core.grabado import (
    DETALLE_ACTIVO,
    DETALLE_FUERA_DE_FACTURA,
    grabar_prestacion,
    to_dict,
)
from app.modules.validaciones.core.medicos import get_medico
from app.modules.validaciones.core.periodos import gate_periodo, periodo_actual, periodo_cerrado
from app.modules.validaciones.legacy import espejo as espejo_legacy
from app.modules.validaciones.schemas import PrestacionCreate

_SOLO_PDF = frozenset({".pdf"})


async def crear_prestacion(
    db: AsyncSession, *, payload: PrestacionCreate, nro_socio: int, usuario_carga: int
) -> dict:
    obra = obras.obtener_o_error(payload.obra_social)
    entrada = parsear_entrada(obra.entrada, payload)

    medico = await get_medico(db, nro_socio)
    obra.verificar_prestador(medico)

    periodo = await periodo_actual(db, obra.nro)
    # Antes de hablar con la O.S.: una autorización puede consumir un recurso
    # real (token, orden) — no se pide si después no se va a poder grabar.
    await gate_periodo(db, obra.nro, periodo)

    ctx = Contexto(
        db=db,
        medico=medico,
        obra_social=obra.nro,
        periodo=periodo,
        fecha=datetime.date.today(),
        usuario_carga=usuario_carga,
    )
    resultado = await obra.validar(ctx, entrada)

    fila = await grabar_prestacion(
        db,
        medico=medico,
        obra_social_id=obra.nro,
        periodo=periodo,
        codigo=resultado.codigo,
        precio=resultado.precio,
        cantidad=entrada.cantidad,
        coseguro=resultado.coseguro,
        nro_afiliado=resultado.nro_afiliado,
        nombre_afiliado=resultado.nombre_afiliado,
        nro_autorizacion=resultado.nro_autorizacion,
        validacion_estado=resultado.estado,
        validacion_detalle=resultado.detalle,
        validacion_respuesta=resultado.traza,
        fecha=ctx.fecha,
        usuario_carga=usuario_carga,
    )

    # TEMPORAL — puente con el sistema viejo. Corre después del commit de
    # `grabar_prestacion` y falla abierto: si no puede espejar, la prestación
    # queda cargada igual. Para apagarlo: borrar esta llamada, la de
    # `eliminar_prestacion` y la carpeta `validaciones/legacy/`.
    await espejo_legacy.replicar_alta(db, fila, medico)

    return to_dict(fila, resultado.precio.descripcion or "")


async def _prestacion_del_socio(
    db: AsyncSession, prestacion_id: int, nro_socio: int
) -> DetalleFacturacionCMC:
    fila = await db.get(DetalleFacturacionCMC, prestacion_id)
    if fila is None or fila.validacion_estado is None or fila.validacion_anulada:
        raise HTTPException(404, "La prestación no existe o ya fue eliminada.")
    if str(fila.cod_med) != str(nro_socio):
        raise HTTPException(403, "La prestación pertenece a otro prestador.")
    return fila


async def adjuntar_orden(
    db: AsyncSession, prestacion_id: int, nro_socio: int, archivo: UploadFile
) -> dict:
    """Guarda la orden/receta en PDF y deja la ruta en `orden_path`."""
    fila = await _prestacion_del_socio(db, prestacion_id, nro_socio)

    # El chequeo anterior era sobre `content_type`, que lo declara el cliente:
    # bastaba mandar el header correcto para guardar cualquier cosa como PDF.
    # `validate_upload` lo decide por magic bytes y además limita el tamaño.
    info = await validate_upload(archivo, _SOLO_PDF)

    destino_dir = os.path.join(UPLOAD_ROOT, "validaciones", str(nro_socio))
    os.makedirs(destino_dir, exist_ok=True)

    nombre = f"{uuid.uuid4().hex}{info.extension}"
    destino = os.path.join(destino_dir, nombre)

    def _escribir() -> None:
        with open(destino, "wb") as f:
            f.write(info.data)

    await run_in_threadpool(_escribir)

    fila.orden_path = destino.replace("\\", "/")
    await db.commit()
    await db.refresh(fila)
    descripciones = await _descripciones(db, [fila])
    return to_dict(fila, descripciones.get(fila.id_detalle_prestaciones, ""))


async def eliminar_prestacion(db: AsyncSession, prestacion_id: int, nro_socio: int) -> None:
    """Baja lógica: la fila queda con `validacion_anulada=1` y `estado='X'` (el
    soft-delete de facturación), así deja de sumar en la factura y en la
    liquidación. No se borra: la traza de lo que la O.S. contestó se conserva.

    Antes de la baja local, le da a la O.S. la chance de anular la autorización
    allá (`ValidadorOS.anular`, default no-op). El lookup es `POR_NRO.get()`,
    **no** `obtener_o_error()`: una fila grabada por una O.S. que después se
    saque del registro tiene que poder darse de baja igual.

    Esa llamada puede **vetar la baja**: si la O.S. exige confirmar la anulación
    de su lado y no la confirma, `anular()` levanta un 409 y la excepción sale
    de acá con la fila intacta — todavía no se tocó nada ni se hizo commit. Hoy
    lo usa Sancor (ver `obras/sancor/validador.py::ValidadorSancor.anular`).
    """
    fila = await _prestacion_del_socio(db, prestacion_id, nro_socio)
    obra_social_id = int(fila.cod_obr)
    if await periodo_cerrado(db, obra_social_id, fila.periodo):
        raise HTTPException(409, "El período ya está cerrado: no se puede eliminar.")

    obra = obras.POR_NRO.get(obra_social_id)
    if obra is not None:
        anulacion = await obra.anular(fila)
        if anulacion is not None:
            # `anulacion.traza` ya viene con la traza original + la clave
            # "anulacion" agregada (ver cada `ValidadorOS.anular`) — se
            # reasigna entera: es columna JSON y SQLAlchemy no detecta
            # mutaciones hechas in situ sobre el dict existente.
            fila.validacion_respuesta = anulacion.traza
            if anulacion.detalle:
                fila.validacion_detalle = anulacion.detalle[:255]

    facturaba = fila.estado == DETALLE_ACTIVO
    fila.validacion_anulada = True
    fila.estado = DETALLE_FUERA_DE_FACTURA
    await db.flush()
    if facturaba:
        # Si era la última prestación abierta del período, se elimina la cabecera
        # abierta (mismo invariante que `anular_prestacion` de facturación).
        await _cleanup_factura_si_vacia(db, fila.cod_obr, fila.periodo)
    await db.commit()

    # TEMPORAL — misma baja lógica en la tabla del sistema viejo (`EXISTE='N'`).
    # Ver la nota de `crear_prestacion`.
    await espejo_legacy.replicar_baja(db, fila)
