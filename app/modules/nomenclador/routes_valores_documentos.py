"""Documentos respaldatorios de las actualizaciones de valores de una O.S.

La pantalla «Historial de Valores» de una obra social mostraba una sola cosa:
la grilla de precios que quedó cargada en `nm_valores` para cada
`vigencia_desde`. Faltaba la otra mitad —**el papel con el que esos precios
llegaron**: la nota de la obra social, el Excel de la lista, el CSV que se
importó—, que hoy vive en el mail de alguien. Acá cada vigencia pasa a tener
los dos registros: los valores y su documento.

La clave es `(obra_social_nro, vigencia_desde)`; el porqué está en el docstring
de `ValorDocumento`. Se admiten varios documentos por vigencia: la OS suele
mandar la nota y la lista por separado.

Va en un router aparte y **montado antes** que `routes_valores.py`: ese módulo
tiene `GET /{id}` con `id: int`, y una ruta con parámetro no cede el match por
que el valor no sea un entero —responde 422—, así que `/documentos` tiene que
declararse primero para que exista.
"""
import datetime
import os
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.common.files import url_archivo
from app.common.uploads import validate_upload
from app.core.config import settings
from app.db.database import get_db
from app.db.models import ListadoMedico, ValorDocumento
from app.modules.nomenclador.schemas_valores_documentos import (
    MesActualizaciones, ObraSocialActualizada, ValorDocumentoOut,
)

router = APIRouter()  # El scope lo declara app/auth/authz.py::SCOPES_POR_RUTA (fuente unica de autorizacion).

#: Bajo `uploads/obras_sociales/`, que ya tiene regla de autorización en
#: app/modules/archivos/routes.py (CATALOGO_LEER, material de catálogo).
DIR_BASE = os.path.join("uploads", "obras_sociales", "valores")

#: Lo que manda una obra social cuando actualiza precios: la nota escaneada o
#: en PDF, la planilla, o el CSV que después se importa.
FORMATOS = frozenset({".pdf", ".xlsx", ".xls", ".csv"})

#: El CSV no tiene magic bytes —es texto pelado—, así que `validate_upload()`
#: lo rechazaría por «no se pudo reconocer el tipo». Se valida aparte: que
#: decodifique como texto es todo lo que se puede afirmar de un CSV, y alcanza
#: para descartar un binario disfrazado.
CSV_CONTENT_TYPE = "text/csv"


async def _leer_csv(archivo: UploadFile) -> tuple[bytes, int]:
    limite = settings.MAX_UPLOAD_BYTES
    await archivo.seek(0)
    data = await archivo.read(limite + 1)
    if len(data) > limite:
        raise HTTPException(
            413, f"El archivo supera el máximo permitido de {limite // (1024 * 1024)} MB."
        )
    if not data:
        raise HTTPException(415, "El archivo está vacío.")
    for codec in ("utf-8-sig", "latin-1"):
        try:
            data.decode(codec)
            return data, len(data)
        except UnicodeDecodeError:
            continue
    raise HTTPException(415, "El .csv no es texto legible (UTF-8 ni latin-1).")


def _to_out(row: ValorDocumento) -> ValorDocumentoOut:
    return ValorDocumentoOut(
        id=row.id,
        obra_social_nro=row.obra_social_nro,
        vigencia_desde=row.vigencia_desde,
        nombre_original=row.nombre_original,
        content_type=row.content_type,
        size=row.size,
        descripcion=row.descripcion,
        url=url_archivo(row.path) or "",
        created_at=row.created_at,
    )


@router.get("/documentos", response_model=List[ValorDocumentoOut])
async def listar_documentos(
    obra_social_nro: int = Query(..., ge=1),
    vigencia_desde: Optional[datetime.date] = Query(
        None, description="Si se omite, devuelve los de todas las vigencias de la O.S."
    ),
    db: AsyncSession = Depends(get_db),
):
    """Documentos de una obra social, la vigencia más reciente primero.

    La pantalla del historial los pide todos de una y los agrupa en memoria,
    igual que hace con los valores: son unos pocos por obra social y así el
    listado no dispara una request por fila.
    """
    stmt = select(ValorDocumento).where(ValorDocumento.obra_social_nro == obra_social_nro)
    if vigencia_desde is not None:
        stmt = stmt.where(ValorDocumento.vigencia_desde == vigencia_desde)
    stmt = stmt.order_by(ValorDocumento.vigencia_desde.desc(), ValorDocumento.id.desc())

    return [_to_out(r) for r in (await db.execute(stmt)).scalars().all()]


@router.post("/documentos", response_model=ValorDocumentoOut, status_code=status.HTTP_201_CREATED)
async def subir_documento(
    obra_social_nro: int = Form(..., ge=1),
    vigencia_desde: datetime.date = Form(..., description="La misma fecha de vigencia de los valores"),
    archivo: UploadFile = File(..., description="PDF, Excel (.xlsx/.xls) o CSV"),
    descripcion: Optional[str] = Form(None, max_length=255),
    db: AsyncSession = Depends(get_db),
    token_user: dict = Depends(get_current_user),
):
    """Adjunta el respaldo de una actualización de valores.

    No se exige que ya existan valores cargados para esa vigencia: el orden
    real es al revés —primero llega la nota de la obra social, después alguien
    carga los precios—, y obligar a que la grilla exista antes forzaría a subir
    el documento en un segundo viaje que nadie hace.
    """
    nombre = archivo.filename or ""
    extension = os.path.splitext(nombre)[1].lower()

    if extension == ".csv":
        data, size = await _leer_csv(archivo)
        content_type = CSV_CONTENT_TYPE
    else:
        # El tipo sale de los magic bytes, no de la extensión del nombre.
        info = await validate_upload(archivo, FORMATOS - {".csv"})
        data, size, extension, content_type = (
            info.data, info.size, info.extension, info.content_type,
        )

    dest_dir = os.path.join(DIR_BASE, str(obra_social_nro))
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, f"{uuid.uuid4().hex}{extension}").replace("\\", "/")

    def _escribir():
        with open(dest_path, "wb") as f:
            f.write(data)

    await run_in_threadpool(_escribir)

    fila = ValorDocumento(
        obra_social_nro=obra_social_nro,
        vigencia_desde=vigencia_desde,
        nombre_original=nombre[:255] or f"documento{extension}",
        path=dest_path,
        content_type=content_type,
        size=size,
        descripcion=(descripcion or "").strip() or None,
        subido_por=await _subido_por(db, token_user),
    )
    db.add(fila)
    try:
        await db.commit()
    except Exception:
        # El archivo ya está en disco y la fila que lo referencia no existe:
        # es basura que nadie va a volver a encontrar. Se borra acá porque es
        # el único momento en que se sabe que quedó suelta.
        try:
            os.remove(dest_path)
        except OSError:
            pass
        raise
    await db.refresh(fila)
    return _to_out(fila)


@router.delete("/documentos/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_documento(
    doc_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Borra el documento y su archivo. No toca los valores de esa vigencia."""
    fila = await db.get(ValorDocumento, doc_id)
    if not fila:
        raise HTTPException(404, "Documento no encontrado")

    ruta = fila.path
    await db.delete(fila)
    await db.commit()

    # Después del commit: si el borrado en base falla, el archivo sigue siendo
    # el respaldo de una fila que quedó viva.
    try:
        if os.path.exists(ruta):
            os.remove(ruta)
    except OSError:
        pass
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _subido_por(db: AsyncSession, token_user: dict) -> Optional[int]:
    """`ListadoMedico.ID` del usuario del token. El JWT sólo trae NRO_SOCIO."""
    try:
        nro_socio = int(token_user["nro_socio"])
    except (KeyError, TypeError, ValueError):
        return None
    return (
        await db.execute(select(ListadoMedico.ID).where(ListadoMedico.NRO_SOCIO == nro_socio))
    ).scalars().first()


@router.get("/actualizaciones", response_model=List[MesActualizaciones])
async def actualizaciones_por_mes(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """Qué obras sociales actualizaron valores, agrupadas por mes de vigencia.

    Agrupa por `vigencia_desde` —cuándo rigen los precios— y no por `created_at`
    —cuándo se cargaron—: una lista de mayo importada en agosto pertenece a mayo,
    que es lo que se está preguntando. Los meses sin actualizaciones no aparecen:
    salen de las filas que existen, no de recorrer un calendario.

    ## Por qué la consulta está partida en tres

    La primera agrupa **sólo sobre `nm_valores`**, sin JOIN y sin ORDER BY. Es
    deliberado: con el JOIN a `obras_sociales` dentro del GROUP BY, MySQL arma
    una tabla temporal y ordena en disco (107 ms sobre 55.000 filas); sin él usa
    `ix_nm_valores_vigencia_os` como índice cubriente y no toca la tabla (6,6 ms).

    Las otras dos resuelven los nombres y los documentos sobre los ~58 pares que
    devolvió la primera. El resultado no crece con el tarifario sino con la
    cantidad de actualizaciones, que son unas pocas por mes; por eso no se pagina.
    """
    filas = (await db.execute(text(
        "SELECT vigencia_desde, obra_social_nro, COUNT(*) "
        "FROM nm_valores GROUP BY vigencia_desde, obra_social_nro"
    ))).all()
    if not filas:
        return []

    nros = {nro for _, nro, _ in filas}
    nombres = dict((await db.execute(
        text("SELECT NRO_OBRASOCIAL, OBRA_SOCIAL FROM obras_sociales "
             "WHERE NRO_OBRASOCIAL IN :nros").bindparams(bindparam("nros", expanding=True)),
        {"nros": list(nros)},
    )).all())

    con_documento = {
        (nro, vig)
        for nro, vig in (await db.execute(text(
            "SELECT obra_social_nro, vigencia_desde FROM nm_valores_documentos"
        ))).all()
    }

    meses: dict[str, MesActualizaciones] = {}
    for vigencia, nro, codigos in filas:
        clave = vigencia.strftime("%Y-%m")
        meses.setdefault(clave, MesActualizaciones(mes=clave)).obras_sociales.append(
            ObraSocialActualizada(
                obra_social_nro=nro,
                # Puede faltar si la O.S. salió del catálogo y sus valores
                # quedaron; mostrar el número es mejor que un renglón vacío.
                nombre=(nombres.get(nro) or "").strip() or f"O.S. {nro}",
                vigencia_desde=vigencia,
                codigos=codigos,
                tiene_documento=(nro, vigencia) in con_documento,
            )
        )

    # Ordenar acá y no en SQL: son ~58 filas y en la consulta costaba un filesort
    # sobre las 55.000 de `nm_valores`.
    for mes in meses.values():
        mes.obras_sociales.sort(key=lambda o: (-o.vigencia_desde.toordinal(), o.nombre))
        mes.total_codigos = sum(o.codigos for o in mes.obras_sociales)
        mes.total_obras_sociales = len({o.obra_social_nro for o in mes.obras_sociales})

    return sorted(meses.values(), key=lambda m: m.mes, reverse=True)
