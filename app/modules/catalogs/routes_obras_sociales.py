import os
import shutil
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.files import url_archivo
from app.common.uploads import DOCUMENTOS, validate_upload
from app.auth.deps import usuario_opcional
from app.auth.scopes import Scope
from app.db.database import get_db
from app.db.models.catalogs import ObrasSociales, ObraSocialContacto, ObraSocialDireccion, ObraSocialDocumento
from app.modules.catalogs.schemas import (
    ContactoSimpleOut,
    DireccionOut,
    DocumentoOut,
    ObraSocialCreate,
    ObraSocialOut,
    ObraSocialSimpleOut,
    ObraSocialUpdate,
)

router = APIRouter()

UPLOAD_DIR = "uploads/obras_sociales"


# ── Helpers ────────────────────────────────────────────────────────────────

def _simple_out(obj: ObrasSociales) -> ObraSocialSimpleOut:
    return ObraSocialSimpleOut(
        id=obj.ID,
        nro_obra_social=obj.NRO_OBRASOCIAL,
        nombre=obj.OBRA_SOCIAL,
        denominacion=f"{obj.NRO_OBRASOCIAL} — {obj.OBRA_SOCIAL}",
    )


async def _load_principal_and_asociadas(
    obj: ObrasSociales, db: AsyncSession
) -> tuple[Optional[ObrasSociales], list[ObrasSociales]]:
    principal = None
    if obj.obra_social_principal_id:
        res = await db.execute(
            select(ObrasSociales).where(ObrasSociales.ID == obj.obra_social_principal_id)
        )
        principal = res.scalar_one_or_none()

    res_asoc = await db.execute(
        select(ObrasSociales).where(ObrasSociales.obra_social_principal_id == obj.ID)
    )
    asociadas = list(res_asoc.scalars().all())
    return principal, asociadas


def _build_out(
    obj: ObrasSociales,
    principal: Optional[ObrasSociales],
    asociadas: list[ObrasSociales],
) -> ObraSocialOut:
    emails = [
        ContactoSimpleOut(valor=c.valor, etiqueta=c.etiqueta)
        for c in obj.contactos
        if c.tipo == "email"
    ]
    telefonos = [
        ContactoSimpleOut(valor=c.valor, etiqueta=c.etiqueta)
        for c in obj.contactos
        if c.tipo == "telefono"
    ]
    direccion = [
        DireccionOut(
            id=d.id,
            provincia=d.provincia,
            localidad=d.localidad,
            direccion=d.direccion,
            codigo_postal=d.codigo_postal,
            horario=d.horario,
        )
        for d in obj.direcciones
    ]
    documentos = [
        DocumentoOut(
            id=doc.id,
            tipo=doc.tipo,
            nombre_custom=doc.nombre_custom,
            # `doc.url` en la base es la ruta de disco; acá sale como URL
            # autorizada. Ojo: os.remove() más abajo usa `doc.url` crudo, así que
            # la transformación va SOLO al serializar.
            url=url_archivo(doc.url),
            created_at=doc.created_at,
        )
        for doc in obj.documentos
    ]
    return ObraSocialOut(
        id=obj.ID,
        nro_obra_social=obj.NRO_OBRASOCIAL,
        nombre=obj.OBRA_SOCIAL,
        denominacion=f"{obj.NRO_OBRASOCIAL} — {obj.OBRA_SOCIAL}",
        marca=obj.MARCA,
        ver_valor=obj.VER_VALOR,
        cuit=obj.cuit,
        direccion_real=obj.direccion_real,
        condicion_iva=obj.condicion_iva,
        plazo_vencimiento=obj.plazo_vencimiento,
        fecha_alta_convenio=obj.fecha_alta_convenio,
        obra_social_principal_id=obj.obra_social_principal_id,
        dia_corte=obj.dia_corte,
        emails=emails,
        telefonos=telefonos,
        obra_social_principal=_simple_out(principal) if principal else None,
        asociadas=[_simple_out(a) for a in asociadas],
        direccion=direccion,
        documentos=documentos,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
    )


async def _batch_principal_and_asociadas(
    objs: list[ObrasSociales], db: AsyncSession
) -> tuple[dict[int, ObrasSociales], dict[int, list[ObrasSociales]]]:
    """Carga principals y asociadas para una lista de obras sociales en dos queries."""
    principal_ids = {o.obra_social_principal_id for o in objs if o.obra_social_principal_id}
    os_ids = {o.ID for o in objs}

    principals_map: dict[int, ObrasSociales] = {}
    if principal_ids:
        res = await db.execute(select(ObrasSociales).where(ObrasSociales.ID.in_(principal_ids)))
        for p in res.scalars().all():
            principals_map[p.ID] = p

    asociadas_map: dict[int, list[ObrasSociales]] = {oid: [] for oid in os_ids}
    if os_ids:
        res = await db.execute(
            select(ObrasSociales).where(ObrasSociales.obra_social_principal_id.in_(os_ids))
        )
        for a in res.scalars().all():
            if a.obra_social_principal_id in asociadas_map:
                asociadas_map[a.obra_social_principal_id].append(a)

    return principals_map, asociadas_map


async def _get_or_404(id: int, db: AsyncSession) -> ObrasSociales:
    res = await db.execute(select(ObrasSociales).where(ObrasSociales.ID == id))
    obj = res.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Obra social no encontrada")
    return obj


# ── Endpoints ──────────────────────────────────────────────────────────────

# Lo que un visitante anónimo SÍ ve de una obra social. Es una **lista blanca**,
# no una lista de lo que se oculta: con la lista negra, agregar un campo nuevo al
# schema lo publicaba por omisión, que es exactamente el modo de falla que este
# trabajo vino a eliminar. Acá, un campo nuevo queda privado hasta que alguien lo
# agregue a mano.
#
# Lo de afuera —CUIT, dirección real, condición de IVA, plazo de vencimiento,
# fecha de alta y día de corte del convenio, emails, teléfonos y la lista de
# convenios en PDF— son datos comerciales o material para armar una base de
# contactos. Los `documentos`, además, ya quedaron detrás de /api/archivos con
# `catalogo:leer`: dejar sus URLs acá sería anunciarlas.
_CAMPOS_OS_PUBLICOS = frozenset({
    "id",
    "nro_obra_social",
    "nombre",
    "denominacion",
    "marca",
    "ver_valor",
    "obra_social_principal_id",
    "obra_social_principal",
    "asociadas",
})


def _recortar_os(out: ObraSocialOut) -> ObraSocialOut:
    """Deja la obra social con lo que puede ver cualquiera.

    Se reconstruye el modelo con solo los campos permitidos: el resto toma el
    default del schema. Blanquear campo por campo no servía —`dia_corte` es
    `int` no nullable y ponerlo en `None` rompe la validación— y además obligaba
    a mantener la lista negra al día.
    """
    datos = out.model_dump()
    return ObraSocialOut(**{k: v for k, v in datos.items() if k in _CAMPOS_OS_PUBLICOS})


@router.get("/", response_model=list[ObraSocialOut])
async def list_obras_sociales(
    db: AsyncSession = Depends(get_db),
    nro_obra_social: Optional[int] = Query(None, description="Filtrar por N° obra social (exacto)"),
    nombre: Optional[str] = Query(None, description="Filtrar por nombre (contiene)"),
    user: dict | None = Depends(usuario_opcional),
):
    """Listado de obras sociales. **Público con respuesta recortada.**

    Lo consume el portal sin login, pero el payload completo incluye CUIT,
    emails, teléfonos, condiciones del convenio y la lista de convenios en PDF.
    Publicar eso entero sería cambiar un 401 por una filtración: al visitante se
    le entrega solo la identificación de la obra social, y el resto requiere
    `catalogo:leer`.
    """
    query = select(ObrasSociales).order_by(ObrasSociales.OBRA_SOCIAL.desc())
    if nro_obra_social is not None:
        query = query.where(ObrasSociales.NRO_OBRASOCIAL == nro_obra_social)
    if nombre is not None:
        query = query.where(ObrasSociales.OBRA_SOCIAL.ilike(f"%{nombre}%"))

    result = await db.execute(query)
    objs = list(result.scalars().all())

    principals_map, asociadas_map = await _batch_principal_and_asociadas(objs, db)

    salida = [
        _build_out(
            obj,
            principals_map.get(obj.obra_social_principal_id) if obj.obra_social_principal_id else None,
            asociadas_map.get(obj.ID, []),
        )
        for obj in objs
    ]

    if Scope.CATALOGO_LEER not in ((user or {}).get("scopes") or []):
        salida = [_recortar_os(o) for o in salida]

    return salida


@router.get("/{id}", response_model=ObraSocialOut)
async def get_obra_social(
    id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    obj = await _get_or_404(id, db)
    principal, asociadas = await _load_principal_and_asociadas(obj, db)
    return _build_out(obj, principal, asociadas)


@router.post("/", response_model=ObraSocialOut, status_code=status.HTTP_201_CREATED)
async def create_obra_social(
    payload: ObraSocialCreate,
    db: AsyncSession = Depends(get_db),
):
    # Verificar unicidad de NRO_OBRASOCIAL
    res = await db.execute(
        select(ObrasSociales).where(ObrasSociales.NRO_OBRASOCIAL == payload.nro_obra_social)
    )
    if res.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Ya existe una obra social con nro_obra_social={payload.nro_obra_social}",
        )

    if payload.obra_social_principal_id is not None:
        res_p = await db.execute(
            select(ObrasSociales).where(ObrasSociales.ID == payload.obra_social_principal_id)
        )
        if not res_p.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="obra_social_principal_id no encontrada")

    obj = ObrasSociales(
        NRO_OBRASOCIAL=payload.nro_obra_social,
        OBRA_SOCIAL=payload.nombre,
        MARCA=payload.marca,
        VER_VALOR=payload.ver_valor,
        cuit=payload.cuit,
        direccion_real=payload.direccion_real,
        condicion_iva=payload.condicion_iva,
        plazo_vencimiento=payload.plazo_vencimiento,
        fecha_alta_convenio=payload.fecha_alta_convenio,
        obra_social_principal_id=payload.obra_social_principal_id,
        dia_corte=payload.dia_corte,
    )
    db.add(obj)
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Conflicto de integridad.") from e

    for c in payload.contactos:
        db.add(ObraSocialContacto(
            obra_social_id=obj.ID, tipo=c.tipo, valor=c.valor, etiqueta=c.etiqueta
        ))
    for d in payload.direcciones:
        db.add(ObraSocialDireccion(
            obra_social_id=obj.ID,
            provincia=d.provincia,
            localidad=d.localidad,
            direccion=d.direccion,
            codigo_postal=d.codigo_postal,
            horario=d.horario,
        ))

    await db.commit()
    await db.refresh(obj)

    principal, asociadas = await _load_principal_and_asociadas(obj, db)
    return _build_out(obj, principal, asociadas)


@router.patch("/{id}", response_model=ObraSocialOut)
async def update_obra_social(
    id: int = Path(..., ge=1),
    payload: ObraSocialUpdate = ...,
    db: AsyncSession = Depends(get_db),
):
    obj = await _get_or_404(id, db)

    if payload.nro_obra_social is not None:
        res = await db.execute(
            select(ObrasSociales).where(
                (ObrasSociales.NRO_OBRASOCIAL == payload.nro_obra_social) & (ObrasSociales.ID != id)
            )
        )
        if res.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail=f"Ya existe otra obra social con nro_obra_social={payload.nro_obra_social}",
            )

    if payload.obra_social_principal_id is not None:
        if payload.obra_social_principal_id == id:
            raise HTTPException(status_code=422, detail="Una obra social no puede ser su propia principal")
        res_p = await db.execute(
            select(ObrasSociales).where(ObrasSociales.ID == payload.obra_social_principal_id)
        )
        if not res_p.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="obra_social_principal_id no encontrada")

    scalar_map = {
        "nro_obra_social": "NRO_OBRASOCIAL",
        "nombre": "OBRA_SOCIAL",
        "marca": "MARCA",
        "ver_valor": "VER_VALOR",
        "cuit": "cuit",
        "direccion_real": "direccion_real",
        "condicion_iva": "condicion_iva",
        "plazo_vencimiento": "plazo_vencimiento",
        "fecha_alta_convenio": "fecha_alta_convenio",
        "obra_social_principal_id": "obra_social_principal_id",
        "dia_corte": "dia_corte",
    }
    changes = payload.model_dump(exclude_unset=True, exclude={"contactos", "direcciones"})
    for field, orm_attr in scalar_map.items():
        if field in changes:
            setattr(obj, orm_attr, changes[field])

    if payload.contactos is not None:
        for c in list(obj.contactos):
            await db.delete(c)
        for c in payload.contactos:
            db.add(ObraSocialContacto(
                obra_social_id=obj.ID, tipo=c.tipo, valor=c.valor, etiqueta=c.etiqueta
            ))

    if payload.direcciones is not None:
        for d in list(obj.direcciones):
            await db.delete(d)
        for d in payload.direcciones:
            db.add(ObraSocialDireccion(
                obra_social_id=obj.ID,
                provincia=d.provincia,
                localidad=d.localidad,
                direccion=d.direccion,
                codigo_postal=d.codigo_postal,
                horario=d.horario,
            ))

    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Conflicto de integridad al actualizar.") from e

    await db.refresh(obj)
    principal, asociadas = await _load_principal_and_asociadas(obj, db)
    return _build_out(obj, principal, asociadas)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_obra_social(
    id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> Response:
    obj = await _get_or_404(id, db)
    # Eliminar archivos físicos de documentos
    for doc in obj.documentos:
        try:
            if os.path.exists(doc.url):
                os.remove(doc.url)
        except OSError:
            pass
    await db.delete(obj)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Documentos ─────────────────────────────────────────────────────────────

@router.post("/{id}/documentos", response_model=DocumentoOut, status_code=status.HTTP_201_CREATED)
async def upload_documento(
    id: int = Path(..., ge=1),
    tipo: str = Form(..., pattern="^(convenio|normas|valores_convenidos|otros)$"),
    nombre_custom: Optional[str] = Form(None),
    archivo: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    obj = await _get_or_404(id, db)

    # Convenios y normas: PDF o imagen escaneada. Valida contenido, no nombre.
    info = await validate_upload(archivo, DOCUMENTOS)

    dest_dir = os.path.join(UPLOAD_DIR, str(id))
    os.makedirs(dest_dir, exist_ok=True)

    filename = f"{uuid.uuid4().hex}{info.extension}"
    dest_path = os.path.join(dest_dir, filename).replace("\\", "/")

    def _write():
        with open(dest_path, "wb") as f:
            f.write(info.data)

    await run_in_threadpool(_write)

    doc = ObraSocialDocumento(
        obra_social_id=obj.ID,
        tipo=tipo,
        url=dest_path,
        nombre_custom=nombre_custom if tipo == "otros" else None,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    return DocumentoOut(
        id=doc.id,
        tipo=doc.tipo,
        nombre_custom=doc.nombre_custom,
        url=url_archivo(doc.url),
        created_at=doc.created_at,
    )


@router.delete("/{id}/documentos/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_documento(
    id: int = Path(..., ge=1),
    doc_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> Response:
    res = await db.execute(
        select(ObraSocialDocumento).where(
            (ObraSocialDocumento.id == doc_id) & (ObraSocialDocumento.obra_social_id == id)
        )
    )
    doc = res.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    try:
        if os.path.exists(doc.url):
            os.remove(doc.url)
    except OSError:
        pass

    await db.delete(doc)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
