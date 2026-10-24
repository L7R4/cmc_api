"""Orquesta el alta y la baja de una prestación contra la obra social
correspondiente. Es el único lugar de `core/` que conoce el registro de
obras sociales (`obras.POR_NRO`) — nada más acá sabe que existen obras
sociales particulares.
"""
import datetime
from typing import Sequence

from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DetalleFacturacionCMC
from app.modules.facturacion import documentacion, historial
from app.modules.facturacion.service import VALIDACION_RECHAZOS, _cleanup_factura_si_vacia
from app.modules.validaciones import obras
from app.modules.validaciones.core.consultas import _descripciones
from app.modules.validaciones.core.contrato import Contexto
from app.modules.validaciones.core.documentos import enriquecer
from app.modules.validaciones.core.entrada import parsear_entrada
from app.modules.validaciones.core.grabado import (
    DETALLE_ACTIVO,
    DETALLE_FUERA_DE_FACTURA,
    grabar_prestacion,
    to_dict,
)
from app.modules.validaciones.core.medicos import get_medico
from app.modules.validaciones.core.periodos import (
    ORIGEN_MEDICO,
    gate_periodo,
    periodo_actual,
    periodo_cerrado,
)
from app.modules.validaciones.legacy import espejo as espejo_legacy
from app.modules.validaciones.schemas import PrestacionCreate

async def crear_prestacion(
    db: AsyncSession, *, payload: PrestacionCreate, nro_socio: int, usuario_carga: int,
    archivos: Sequence[UploadFile] = (),
) -> dict:
    obra = obras.obtener_o_error(payload.obra_social)
    entrada = parsear_entrada(obra.entrada, payload)

    medico = await get_medico(db, nro_socio)
    obra.verificar_prestador(medico)

    periodo = await periodo_actual(db, obra.nro)
    # Antes de hablar con la O.S.: una autorización puede consumir un recurso
    # real (token, orden) — no se pide si después no se va a poder grabar.
    await gate_periodo(db, obra.nro, periodo)

    # Documentación digital: si la obra social y el código la exigen, sin archivos no se valida.
    # Va antes de hablar con la O.S. por lo mismo que el gate de arriba. Los archivos también
    # se verifican acá, por su contenido real.
    infos = await documentacion.validar_archivos(archivos)
    if not infos and await documentacion.requiere_documentacion(db, obra.nro, payload.codigo):
        raise HTTPException(
            422, "Esta prestación exige documentación digital: adjuntá al menos un archivo.",
        )

    ctx = Contexto(
        db=db,
        medico=medico,
        obra_social=obra.nro,
        periodo=periodo,
        fecha=datetime.date.today(),
        usuario_carga=usuario_carga,
    )
    resultado = await obra.validar(ctx, entrada)

    escritos = await documentacion.escribir(infos, medico.NRO_SOCIO) if infos else []
    try:
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
            documentos=escritos,
        )
    except Exception:
        await db.rollback()
        documentacion.borrar_archivos(e.path for e in escritos)
        raise

    # TEMPORAL — puente con el sistema viejo. Corre después del commit de
    # `grabar_prestacion` y falla abierto: si no puede espejar, la prestación
    # queda cargada igual. Para apagarlo: borrar esta llamada, la de
    # `eliminar_prestacion` y la carpeta `validaciones/legacy/`.
    await espejo_legacy.replicar_alta(db, fila, medico)

    salida = to_dict(fila, resultado.precio.descripcion or "")
    await enriquecer(db, [fila], [salida])
    return salida


async def _prestacion_del_socio(
    db: AsyncSession, prestacion_id: int, nro_socio: int
) -> DetalleFacturacionCMC:
    fila = await db.get(DetalleFacturacionCMC, prestacion_id)
    if fila is None or fila.validacion_estado is None or fila.validacion_anulada:
        raise HTTPException(404, "La prestación no existe o ya fue eliminada.")
    # La que eliminó el Colegio desde facturación tampoco es del panel: el médico ve las
    # rechazadas (también en X), no las eliminadas.
    if fila.estado == DETALLE_FUERA_DE_FACTURA and fila.validacion_estado not in VALIDACION_RECHAZOS:
        raise HTTPException(404, "La prestación no existe o ya fue eliminada.")
    if str(fila.cod_med) != str(nro_socio):
        raise HTTPException(403, "La prestación pertenece a otro prestador.")
    return fila


async def _exigir_periodo_abierto(
    db: AsyncSession, fila: DetalleFacturacionCMC, actor: str, accion: str,
) -> None:
    """409 si `actor` ya no puede operar el período de la prestación — mismo criterio
    que la baja (`periodo_cerrado`): la fase médico cerrada bloquea al médico, y el
    Colegio sigue pudiendo mientras su propia fase siga abierta."""
    if await periodo_cerrado(db, int(fila.cod_obr), fila.periodo, actor):
        raise HTTPException(409, f"El período ya está cerrado: no se puede {accion}.")


async def _salida(db: AsyncSession, fila: DetalleFacturacionCMC) -> dict:
    descripciones = await _descripciones(db, [fila])
    salida = to_dict(fila, descripciones.get(fila.id_detalle_prestaciones, ""))
    await enriquecer(db, [fila], [salida])
    return salida


async def listar_documentos(db: AsyncSession, prestacion_id: int, nro_socio: int) -> list[dict]:
    fila = await _prestacion_del_socio(db, prestacion_id, nro_socio)
    return [documentacion.doc_out(d) for d in await documentacion.listar(db, fila.id_detalle_prestaciones)]


async def agregar_documentos(
    db: AsyncSession, prestacion_id: int, nro_socio: int, archivos: Sequence[UploadFile],
    origen: str, subido_por,
) -> dict:
    """Suma archivos a la documentación de la prestación (PDF, JPG o PNG, por su contenido real)."""
    fila = await _prestacion_del_socio(db, prestacion_id, nro_socio)
    await _exigir_periodo_abierto(db, fila, origen, "agregar documentación")
    await documentacion.agregar(db, fila, archivos, origen, subido_por)
    await db.refresh(fila)
    return await _salida(db, fila)


async def adjuntar_orden(
    db: AsyncSession, prestacion_id: int, nro_socio: int, archivo: UploadFile,
    origen: str = "medico", subido_por=None,
) -> dict:
    """Alias de `agregar_documentos` para un solo archivo (el endpoint `/orden` anterior)."""
    return await agregar_documentos(
        db, prestacion_id, nro_socio, [archivo], origen, subido_por if subido_por is not None else nro_socio,
    )


async def reemplazar_documento(
    db: AsyncSession, prestacion_id: int, nro_socio: int, doc_id: int, archivo: UploadFile,
    origen: str, subido_por,
) -> dict:
    fila = await _prestacion_del_socio(db, prestacion_id, nro_socio)
    await _exigir_periodo_abierto(db, fila, origen, "reemplazar documentación")
    await documentacion.reemplazar(db, fila, doc_id, archivo, origen, subido_por)
    await db.refresh(fila)
    return await _salida(db, fila)


async def eliminar_documento(
    db: AsyncSession, prestacion_id: int, nro_socio: int, doc_id: int, actor: str = ORIGEN_MEDICO,
) -> dict:
    """El médico no puede dejar la prestación sin documentos si los exige (409)."""
    fila = await _prestacion_del_socio(db, prestacion_id, nro_socio)
    await _exigir_periodo_abierto(db, fila, actor, "eliminar documentación")
    await documentacion.eliminar(db, fila, doc_id, es_medico=True)
    await db.refresh(fila)
    return await _salida(db, fila)


async def eliminar_prestacion(
    db: AsyncSession, prestacion_id: int, nro_socio: int, actor: str = ORIGEN_MEDICO,
) -> None:
    """Baja lógica: la fila queda con `validacion_anulada=1` y `estado='X'` (el
    soft-delete de facturación), así deja de sumar en la factura y en la
    liquidación. No se borra: la traza de lo que la O.S. contestó se conserva.

    `actor` distingue quién pide la baja: el médico dueño del token, o el
    Colegio operando en su nombre (ver `periodos.actor_para`). El período
    médico cerrado solo bloquea al actor médico — el Colegio puede seguir
    dando de baja mientras su propia fase del período siga abierta.

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
    if await periodo_cerrado(db, obra_social_id, fila.periodo, actor):
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
    estado_antes = fila.estado
    fila.validacion_anulada = True
    fila.estado = DETALLE_FUERA_DE_FACTURA
    # Sin motivo: el médico no lo carga. Si la eliminó una rechazada, el estado ya era X.
    await historial.registrar(db, [historial.evento(
        fila, historial.ELIMINADA, estado_antes=estado_antes, estado_despues=DETALLE_FUERA_DE_FACTURA,
        contexto=historial.contexto_de(fila, origen="validaciones"),
    )])
    await db.flush()
    if facturaba:
        # Si era la última prestación abierta del período, se elimina la cabecera
        # abierta (mismo invariante que `anular_prestacion` de facturación).
        await _cleanup_factura_si_vacia(db, fila.cod_obr, fila.periodo)
    await db.commit()

    # TEMPORAL — misma baja lógica en la tabla del sistema viejo (`EXISTE='N'`).
    # Ver la nota de `crear_prestacion`.
    await espejo_legacy.replicar_baja(db, fila)
