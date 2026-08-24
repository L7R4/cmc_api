"""Registro de deuda de una obra social, con la factura adjunta.

Es la pestaña «Pagos» de su perfil: qué nos debe, de qué fecha, en qué estado.

Tres campos y nada más, como lo pidió el Colegio. Un modelo más rico —concepto,
período, vencimiento, monto cobrado— habría dado columnas vacías.

`estado` se guarda: sin un "monto cobrado" contra el cual comparar, no hay de
dónde derivarlo.

La factura se sube aparte del alta (`POST /pagos/{id}/factura`), así editar un
monto no puede borrarla por mandar el campo de archivo vacío. Va a
`uploads/obras_sociales/<id>/`, que ya está detrás de `/api/archivos` con regla
de autorización propia.

Módulo aparte de `routes_obras_sociales.py` aunque comparta prefijo: es todo
agregado, no toca lo que ya andaba. Permisos: `catalogo:leer` / `catalogo:editar`.
"""
import decimal
import os
import uuid

from fastapi import (
    APIRouter, Depends, File, HTTPException, Path, Response, UploadFile, status,
)
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.common.files import url_archivo
from app.common.uploads import DOCUMENTOS, validate_upload
from app.db.database import get_db
from app.db.models import ObrasSociales, ObraSocialPago
from app.modules.catalogs.schemas_os_pagos import PagoIn, PagoOut, PagosPage, ResumenPagos

router = APIRouter()  # El scope lo declara app/auth/authz.py::SCOPES_POR_RUTA (fuente unica de autorizacion).

#: Mismo directorio que los documentos del convenio. Ver el docstring.
UPLOAD_DIR = "uploads/obras_sociales"

CERO = decimal.Decimal("0.00")


async def _obra_o_404(obra_id: int, db: AsyncSession) -> ObrasSociales:
    obra = await db.get(ObrasSociales, obra_id)
    if not obra:
        raise HTTPException(404, "Obra social no encontrada")
    return obra


async def _pago_o_404(obra_id: int, pago_id: int, db: AsyncSession) -> ObraSocialPago:
    """La fila, verificando que sea de esa obra social.

    El chequeo del `obra_social_id` no es decorativo: sin él,
    `PUT /obras_social/5/pagos/99` editaría el pago 99 aunque sea de otra obra
    social, y la URL diría una cosa mientras pasa otra.
    """
    fila = await db.get(ObraSocialPago, pago_id)
    if not fila or fila.obra_social_id != obra_id:
        raise HTTPException(404, "Pago no encontrado")
    return fila


def _out(fila: ObraSocialPago) -> PagoOut:
    salida = PagoOut.model_validate(fila)
    salida.factura_url = url_archivo(fila.factura_url) if fila.factura_url else None
    return salida


def _marcar(fila: ObraSocialPago, usuario: dict) -> None:
    uid = usuario.get("uid")
    fila.actualizado_por = int(uid) if uid is not None else None


@router.get("/{obra_id}/pagos", response_model=PagosPage)
async def listar_pagos(
    obra_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """Las deudas de la obra social, la más reciente primero, con los totales.

    Ordena por `fecha DESC` y desempata por `id DESC`: varias deudas pueden
    compartir fecha y sin el desempate quedarían en orden distinto en cada
    consulta.
    """
    await _obra_o_404(obra_id, db)

    stmt = (
        select(ObraSocialPago)
        .where(ObraSocialPago.obra_social_id == obra_id)
        .order_by(ObraSocialPago.fecha.desc(), ObraSocialPago.id.desc())
    )
    filas = (await db.execute(stmt)).scalars().all()
    items = [_out(f) for f in filas]

    pagado = sum((i.monto for i in items if i.estado == "pagado"), CERO)
    total = sum((i.monto for i in items), CERO)
    resumen = ResumenPagos(
        total=total,
        pagado=pagado,
        # Se resta en vez de volver a sumar los no-pagados: así las dos cifras
        # cierran contra el total por construcción y no por coincidencia.
        adeudado=total - pagado,
        pendientes=sum(1 for i in items if i.estado != "pagado"),
    )
    return PagosPage(items=items, resumen=resumen)


@router.post("/{obra_id}/pagos", response_model=PagoOut, status_code=status.HTTP_201_CREATED)
async def crear_pago(
    body: PagoIn,
    obra_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    usuario=Depends(get_current_user),
):
    """Registra una deuda. La factura se adjunta después, con su endpoint."""
    await _obra_o_404(obra_id, db)

    fila = ObraSocialPago(obra_social_id=obra_id, **body.model_dump())
    _marcar(fila, usuario)
    db.add(fila)
    await db.commit()
    await db.refresh(fila)
    return _out(fila)


@router.put("/{obra_id}/pagos/{pago_id}", response_model=PagoOut)
async def editar_pago(
    body: PagoIn,
    obra_id: int = Path(..., ge=1),
    pago_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    usuario=Depends(get_current_user),
):
    """Reemplaza fecha, monto y estado. **La factura adjunta queda como estaba.**

    `PagoIn` no tiene campo de archivo, así que eso es cierto por construcción y
    no porque este handler se acuerde de excluirlo.
    """
    fila = await _pago_o_404(obra_id, pago_id, db)

    for campo, valor in body.model_dump().items():
        setattr(fila, campo, valor)
    _marcar(fila, usuario)

    await db.commit()
    await db.refresh(fila)
    return _out(fila)


@router.post("/{obra_id}/pagos/{pago_id}/factura", response_model=PagoOut)
async def subir_factura(
    obra_id: int = Path(..., ge=1),
    pago_id: int = Path(..., ge=1),
    archivo: UploadFile = File(..., description="PDF o imagen de la factura"),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """Adjunta (o reemplaza) la factura. PDF o imagen escaneada.

    El tipo sale de los magic bytes y no de la extensión declarada, que no
    prueba nada. `DOCUMENTOS` ya cubre PDF, JPG, PNG, WEBP y TIFF, que es
    exactamente lo que llega de una factura: o el PDF original, o la foto que
    alguien le sacó al papel.

    Si ya había una, el archivo anterior se borra del disco: es un reemplazo, y
    dejarlo suelto sería basura que nadie referencia.
    """
    fila = await _pago_o_404(obra_id, pago_id, db)
    info = await validate_upload(archivo, DOCUMENTOS)

    dest_dir = os.path.join(UPLOAD_DIR, str(obra_id))
    os.makedirs(dest_dir, exist_ok=True)

    # Nombre inadivinable en disco: es documentación comercial de un tercero, no
    # contenido público. El nombre original se guarda aparte, para mostrarlo.
    filename = f"factura_{uuid.uuid4().hex}{info.extension}"
    dest_path = os.path.join(dest_dir, filename).replace("\\", "/")

    def _write():
        with open(dest_path, "wb") as f:
            f.write(info.data)

    await run_in_threadpool(_write)

    anterior = fila.factura_url
    fila.factura_url = dest_path
    fila.factura_nombre = info.original_name

    try:
        await db.commit()
    except Exception:
        # Si la fila no se pudo guardar, el archivo nuevo no lo referencia
        # nadie. Se limpia acá, que es el único momento en que se sabe.
        if os.path.exists(dest_path):
            os.remove(dest_path)
        raise

    if anterior and anterior != dest_path and os.path.exists(anterior):
        os.remove(anterior)

    await db.refresh(fila)
    return _out(fila)


@router.delete("/{obra_id}/pagos/{pago_id}/factura", response_model=PagoOut)
async def borrar_factura(
    obra_id: int = Path(..., ge=1),
    pago_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """Saca la factura sin borrar el registro de la deuda."""
    fila = await _pago_o_404(obra_id, pago_id, db)

    ruta = fila.factura_url
    fila.factura_url = None
    fila.factura_nombre = None
    await db.commit()

    if ruta and os.path.exists(ruta):
        os.remove(ruta)

    await db.refresh(fila)
    return _out(fila)


@router.delete("/{obra_id}/pagos/{pago_id}", status_code=status.HTTP_204_NO_CONTENT)
async def borrar_pago(
    obra_id: int = Path(..., ge=1),
    pago_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
) -> Response:
    """Borra el registro y su factura.

    Borrado real y no lógico: acá no hay historia que preservar como en un
    feriado o una planilla. Una deuda cargada mal es un error de tipeo, y
    dejarla marcada de baja sólo ensuciaría el total. Quién la borró queda en
    `audit_log`.
    """
    fila = await _pago_o_404(obra_id, pago_id, db)

    ruta = fila.factura_url
    await db.delete(fila)
    await db.commit()

    if ruta and os.path.exists(ruta):
        os.remove(ruta)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
