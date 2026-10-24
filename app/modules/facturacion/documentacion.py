"""Documentación digital de las prestaciones.

Una prestación puede llevar varios archivos (orden, bono, ticket…) en
`detalle_facturacion_documento`. Cuándo los EXIGE sale de dos marcas que tienen que estar
activas a la vez: `obras_sociales.documentacion_digital` y
`nm_codigo_obra_social.requiere_documentacion`.

Quién los sube: el médico desde la integración (obligatorio si corresponde) o el Colegio
desde facturación (opcional: queda «falta» y se completa después). Los archivos viven bajo
`uploads/validaciones/<cod_med>/`, que es donde ya estaban las órdenes: así el acceso lo
resuelve la regla que ya existe en `archivos/routes.py` (el médico dueño y el personal del
Colegio con `medico:leer`), sin una sección nueva que declarar.

Ningún helper de acá hace commit: corre dentro de la transacción de quien llama. Los que
escriben a disco devuelven lo escrito para que, si el commit falla, el que llama pueda
limpiarlo con `borrar_archivos`.
"""
import os
import uuid
from dataclasses import dataclass
from typing import Optional, Sequence

from fastapi import HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.files import UPLOAD_ROOT
from app.common.uploads import UploadInfo, validate_upload
from app.db.models import CodigoObraSocial, DetalleDocumento, DetalleFacturacionCMC, ObrasSociales
from app.modules.facturacion import historial

# Formatos que entran al PDF de la factura: las fotos se convierten en página.
EXTENSIONES = frozenset({".pdf", ".jpg", ".png"})
MAX_ARCHIVO_BYTES = 5 * 1024 * 1024
MAX_POR_PRESTACION = 15

NO_APLICA, FALTA, COMPLETA = "no_aplica", "falta", "completa"
ORIGENES = ("medico", "colegio")


@dataclass(frozen=True)
class ArchivoEscrito:
    """Un archivo ya en disco, a la espera de que su fila se confirme en la base."""
    path: str  # relativo a la raíz del proyecto, como el resto de `uploads/…`
    nombre_original: str
    content_type: str
    size: int


# ── Quién la exige ────────────────────────────────────────────────────────────
async def obra_social_con_documentacion(db: AsyncSession, cod_obr) -> bool:
    """La obra social (NRO_OBRASOCIAL) trabaja con documentación digital."""
    try:
        nro = int(cod_obr)
    except (TypeError, ValueError):
        return False
    return bool((await db.execute(
        select(ObrasSociales.documentacion_digital).where(ObrasSociales.NRO_OBRASOCIAL == nro)
    )).scalar())


async def codigos_que_requieren(db: AsyncSession, cod_obr, codigos: Sequence[str]) -> set[str]:
    """De estos códigos, los que exigen documentación en esa obra social. Vacío si la obra
    social no la tiene habilitada."""
    if not codigos or not await obra_social_con_documentacion(db, cod_obr):
        return set()
    filas = await db.execute(
        select(CodigoObraSocial.codigo).where(
            CodigoObraSocial.obra_social_nro == int(cod_obr),
            CodigoObraSocial.codigo.in_(set(codigos)),
            CodigoObraSocial.requiere_documentacion.is_(True),
        )
    )
    return {c for (c,) in filas.all()}


async def requiere_documentacion(db: AsyncSession, cod_obr, codigo: Optional[str]) -> bool:
    if not codigo:
        return False
    return codigo in await codigos_que_requieren(db, cod_obr, [codigo])


async def exige(db: AsyncSession, detalle: DetalleFacturacionCMC) -> bool:
    return await requiere_documentacion(db, detalle.cod_obr, detalle.cod_nom)


# ── Lectura ───────────────────────────────────────────────────────────────────
async def listar(db: AsyncSession, detalle_id: int) -> list[DetalleDocumento]:
    return list((await db.execute(
        select(DetalleDocumento)
        .where(DetalleDocumento.detalle_id == detalle_id)
        .order_by(DetalleDocumento.posicion, DetalleDocumento.id)
    )).scalars().all())


async def contar(db: AsyncSession, detalle_ids: Sequence[int]) -> dict[int, int]:
    """Cantidad de documentos por prestación (sólo las que tienen alguno)."""
    if not detalle_ids:
        return {}
    filas = await db.execute(
        select(DetalleDocumento.detalle_id, func.count())
        .where(DetalleDocumento.detalle_id.in_(set(detalle_ids)))
        .group_by(DetalleDocumento.detalle_id)
    )
    return {i: n for i, n in filas.all()}


def estado_de(exige_doc: bool, cantidad: int) -> str:
    if not exige_doc:
        return NO_APLICA
    return COMPLETA if cantidad > 0 else FALTA


async def estado(db: AsyncSession, detalle: DetalleFacturacionCMC) -> str:
    n = (await contar(db, [detalle.id_detalle_prestaciones])).get(detalle.id_detalle_prestaciones, 0)
    return estado_de(await exige(db, detalle), n)


# ── Escritura en disco ────────────────────────────────────────────────────────
async def validar_archivos(archivos: Sequence[UploadFile], *, ya_hay: int = 0) -> list[UploadInfo]:
    """Valida cada archivo por su contenido real (no por lo que declara el cliente)."""
    archivos = [a for a in archivos if a is not None and (a.filename or "")]
    if ya_hay + len(archivos) > MAX_POR_PRESTACION:
        raise HTTPException(409, f"Una prestación admite hasta {MAX_POR_PRESTACION} documentos.")
    return [await validate_upload(a, EXTENSIONES, max_bytes=MAX_ARCHIVO_BYTES) for a in archivos]


def _escribir_sync(infos: Sequence[UploadInfo], cod_med: str) -> list[ArchivoEscrito]:
    destino_dir = os.path.join(UPLOAD_ROOT, "validaciones", str(cod_med))
    os.makedirs(destino_dir, exist_ok=True)
    escritos: list[ArchivoEscrito] = []
    try:
        for info in infos:
            destino = os.path.join(destino_dir, f"{uuid.uuid4().hex}{info.extension}")
            with open(destino, "wb") as f:
                f.write(info.data)
            escritos.append(ArchivoEscrito(
                path=destino.replace("\\", "/"),
                nombre_original=info.original_name[:255],
                content_type=info.content_type,
                size=info.size,
            ))
    except Exception:
        borrar_archivos(e.path for e in escritos)
        raise
    return escritos


async def escribir(infos: Sequence[UploadInfo], cod_med) -> list[ArchivoEscrito]:
    return await run_in_threadpool(_escribir_sync, infos, str(cod_med))


def borrar_archivos(paths) -> None:
    """Borra del disco lo que quedó sin fila (o la fila que se dio de baja). Nunca falla."""
    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass


# ── Alta, reemplazo, baja y orden ─────────────────────────────────────────────
async def _siguiente_posicion(db: AsyncSession, detalle_id: int) -> int:
    ultima = (await db.execute(
        select(func.max(DetalleDocumento.posicion)).where(DetalleDocumento.detalle_id == detalle_id)
    )).scalar()
    return 0 if ultima is None else int(ultima) + 1


async def registrar(
    db: AsyncSession, detalle_id: int, escritos: Sequence[ArchivoEscrito], origen: str, subido_por,
) -> list[DetalleDocumento]:
    """Inserta las filas de lo ya escrito. Sin commit: va en la transacción de quien llama."""
    assert origen in ORIGENES
    posicion = await _siguiente_posicion(db, detalle_id)
    filas = []
    for i, e in enumerate(escritos):
        fila = DetalleDocumento(
            detalle_id=detalle_id, posicion=posicion + i, path=e.path,
            nombre_original=e.nombre_original, content_type=e.content_type, size=e.size,
            origen=origen, subido_por=str(subido_por)[:30] if subido_por is not None else None,
        )
        db.add(fila)
        filas.append(fila)
    await db.flush()
    return filas


async def agregar(
    db: AsyncSession, detalle: DetalleFacturacionCMC, archivos: Sequence[UploadFile], origen: str, subido_por,
) -> list[DetalleDocumento]:
    """Valida, escribe, registra y confirma. Si algo falla, no queda nada en disco."""
    ya_hay = len(await listar(db, detalle.id_detalle_prestaciones))
    infos = await validar_archivos(archivos, ya_hay=ya_hay)
    if not infos:
        raise HTTPException(422, "No se recibió ningún archivo.")
    escritos = await escribir(infos, detalle.cod_med)
    try:
        filas = await registrar(db, detalle.id_detalle_prestaciones, escritos, origen, subido_por)
        await historial.registrar(db, [
            historial.evento(
                detalle, historial.DOCUMENTO_AGREGADO,
                contexto=historial.contexto_de(detalle, documento_id=f.id, archivo=f.nombre_original),
            )
            for f in filas
        ])
        await db.commit()
    except Exception:
        await db.rollback()
        borrar_archivos(e.path for e in escritos)
        raise
    return filas


async def _documento(db: AsyncSession, detalle_id: int, doc_id: int) -> DetalleDocumento:
    doc = await db.get(DetalleDocumento, doc_id)
    if doc is None or doc.detalle_id != detalle_id:
        raise HTTPException(404, "El documento no existe en esta prestación.")
    return doc


async def reemplazar(
    db: AsyncSession, detalle: DetalleFacturacionCMC, doc_id: int, archivo: UploadFile, origen: str, subido_por,
) -> DetalleDocumento:
    """Cambia el archivo de un documento conservando su lugar en el orden."""
    doc = await _documento(db, detalle.id_detalle_prestaciones, doc_id)
    (info,) = await validar_archivos([archivo])
    (escrito,) = await escribir([info], detalle.cod_med)
    anterior = doc.path
    try:
        await historial.registrar(db, [historial.evento(
            detalle, historial.DOCUMENTO_REEMPLAZADO,
            cambios={"archivo": [doc.nombre_original, escrito.nombre_original]},
            contexto=historial.contexto_de(detalle, documento_id=doc.id),
        )])
        doc.path, doc.nombre_original = escrito.path, escrito.nombre_original
        doc.content_type, doc.size = escrito.content_type, escrito.size
        doc.origen = origen
        doc.subido_por = str(subido_por)[:30] if subido_por is not None else None
        await db.commit()
    except Exception:
        await db.rollback()
        borrar_archivos([escrito.path])
        raise
    borrar_archivos([anterior])
    return doc


async def eliminar(
    db: AsyncSession, detalle: DetalleFacturacionCMC, doc_id: int, *, es_medico: bool,
) -> None:
    """Da de baja un documento.

    El médico no puede dejar sin documentos una prestación que los exige (la validó con
    ellos). El Colegio sí, y en ese caso la prestación vuelve a «sin auditar»: lo auditado ya
    no es lo que hay.
    """
    doc = await _documento(db, detalle.id_detalle_prestaciones, doc_id)
    restantes = len(await listar(db, detalle.id_detalle_prestaciones)) - 1
    exigida = await exige(db, detalle)
    eventos = [historial.evento(
        detalle, historial.DOCUMENTO_QUITADO,
        contexto=historial.contexto_de(detalle, documento_id=doc.id, archivo=doc.nombre_original),
    )]
    if exigida and restantes == 0:
        if es_medico:
            raise HTTPException(409, "Tiene que quedar al menos un documento.")
        if detalle.revisado:
            eventos.append(historial.evento(
                detalle, historial.DESAUDITADA, motivo_texto="Se quitó el último documento.",
            ))
        detalle.revisado = False
    await historial.registrar(db, eventos)
    path = doc.path
    await db.delete(doc)
    await db.commit()
    borrar_archivos([path])


async def reordenar(db: AsyncSession, detalle_id: int, ids: Sequence[int]) -> list[DetalleDocumento]:
    """`ids` es el orden nuevo y tiene que traer todos los documentos de la prestación."""
    actuales = await listar(db, detalle_id)
    if sorted(ids) != sorted(d.id for d in actuales):
        raise HTTPException(422, "La lista tiene que incluir exactamente los documentos de la prestación.")
    por_id = {d.id: d for d in actuales}
    for pos, i in enumerate(ids):
        por_id[i].posicion = pos
    await db.commit()
    return await listar(db, detalle_id)


# ── En bloque (listados) ──────────────────────────────────────────────────────
async def por_detalle(db: AsyncSession, detalle_ids: Sequence[int]) -> dict[int, list[DetalleDocumento]]:
    """Documentos de varias prestaciones, ordenados, agrupados por prestación."""
    if not detalle_ids:
        return {}
    filas = (await db.execute(
        select(DetalleDocumento)
        .where(DetalleDocumento.detalle_id.in_(set(detalle_ids)))
        .order_by(DetalleDocumento.detalle_id, DetalleDocumento.posicion, DetalleDocumento.id)
    )).scalars().all()
    out: dict[int, list[DetalleDocumento]] = {}
    for d in filas:
        out.setdefault(d.detalle_id, []).append(d)
    return out


def es_integrante_de_equipo(grupo_equipo_id, propio_id) -> bool:
    """Ayudante, pediatra o gastos de una cirugía: apuntan a su cabeza (`grupo_equipo_id`). Los
    documentos son los de la cabeza (la orden de la práctica), así que ellos no los exigen aparte."""
    return grupo_equipo_id is not None and int(grupo_equipo_id) != int(propio_id)


async def exigen_en_bloque(db: AsyncSession, filas: Sequence[DetalleFacturacionCMC]) -> set[int]:
    """Ids de las prestaciones (de estas filas) cuyo código exige documentación en su obra social.
    El equipo de una cirugía no cuenta: la exige su cabeza."""
    por_os: dict[str, set[str]] = {}
    for f in filas:
        if f.cod_nom:
            por_os.setdefault(str(f.cod_obr), set()).add(f.cod_nom)
    requeridos: dict[str, set[str]] = {}
    for os_nro, codigos in por_os.items():
        requeridos[os_nro] = await codigos_que_requieren(db, os_nro, list(codigos))
    return {
        f.id_detalle_prestaciones for f in filas
        if f.cod_nom in requeridos.get(str(f.cod_obr), set())
        and not es_integrante_de_equipo(f.grupo_equipo_id, f.id_detalle_prestaciones)
    }


def doc_out(d: DetalleDocumento) -> dict:
    """Un documento como lo consume el front. `url` pide token: se abre con `abrirAdjunto()`."""
    from app.common.files import url_archivo
    return {
        "id": d.id, "nombre": d.nombre_original, "url": url_archivo(d.path),
        "content_type": d.content_type, "size": d.size, "origen": d.origen,
        "posicion": d.posicion, "created_at": d.created_at,
    }
