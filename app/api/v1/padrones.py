from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db
from app.db.models import MedicoObraSocial, ListadoMedico, ObrasSociales
from app.schemas.padrones_schema import ObraSocialOut, PadronOut, PadronUpdate

router = APIRouter()

# Helpers de compatibilidad por si tu modelo usa NRO_OBRA_SOCIAL vs NRO_OBRASOCIAL
def _os_number_col():
    # columna del número en el catálogo
    return getattr(ObrasSociales, "NRO_OBRA_SOCIAL", getattr(ObrasSociales, "NRO_OBRASOCIAL"))

def _padron_number_attr():
    # atributo del número de OS en MedicoObraSocial
    return getattr(MedicoObraSocial, "NRO_OBRASOCIAL")

async def _listado_defaults(db: AsyncSession, nro_socio: int):
    lm = (await db.execute(select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == nro_socio))).scalar_one_or_none()
    if not lm:
        return {}
    return {
        "NOMBRE": getattr(lm, "NOMBRE", None),
        "MATRICULA_PROV": getattr(lm, "MATRICULA_PROV", None),
        "MATRICULA_NAC": getattr(lm, "MATRICULA_NAC", None),
        "TELEFONO_CONSULTA": getattr(lm, "TELEFONO_CONSULTA", None),
    }

# 1) Catálogo: listar obras sociales con MARCA = "S"
@router.get("/catalogo", response_model=List[ObraSocialOut])
async def catalogo_obras_sociales(
    marca: str = Query("S", description='Filtrar por MARCA; por defecto "S"'),
    db: AsyncSession = Depends(get_async_db),
):
    nro_col = _os_number_col()
    nombre_col = getattr(ObrasSociales, "OBRA_SOCIAL", None)
    if nombre_col is None:
        raise HTTPException(status_code=500, detail="No encuentro columna de nombre en ObrasSociales")

    stmt = select(nro_col.label("nro"), nombre_col.label("nombre")).where(ObrasSociales.MARCA == marca).order_by(nombre_col.asc())
    rows = (await db.execute(stmt)).all()

    out: list[ObraSocialOut] = []
    for nro, nombre in rows:
        codigo = None
        # si no tenés un campo código en la tabla, generamos "OS{n:03d}" opcional
        try:
            nint = int(nro)
            codigo = f"OS {nint:03d}"
        except Exception:
            pass
        out.append(ObraSocialOut(NRO_OBRA_SOCIAL=nro, NOMBRE=nombre, CODIGO=codigo))
    return out

# 2) Listar vínculos del médico
@router.get("/{nro_socio}", response_model=List[PadronOut])
async def list_padrones_de_medico(
    nro_socio: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
):
    stmt = select(MedicoObraSocial).where(MedicoObraSocial.NRO_SOCIO == nro_socio).order_by(MedicoObraSocial.ID.desc())
    return list((await db.execute(stmt)).scalars())

# 3) PUT idempotente por checkbox: crea si no existe (por NRO_SOCIO + NRO_OBRASOCIAL), actualiza si existe
@router.put("/{nro_socio}/obras-sociales/{nro_os}", response_model=PadronOut, status_code=status.HTTP_200_OK)
async def upsert_padron_checkbox(
    nro_socio: int = Path(..., ge=1),
    nro_os: int = Path(..., ge=1),
    body: Optional[PadronUpdate] = None,
    db: AsyncSession = Depends(get_async_db),
):
    nro_col = _os_number_col()
    # validamos que la obra social exista y esté activa (MARCA='S')
    os_row = (await db.execute(
        select(ObrasSociales).where(and_(nro_col == nro_os, ObrasSociales.MARCA == "S"))
    )).scalar_one_or_none()
    if not os_row:
        raise HTTPException(status_code=404, detail="Obra social no encontrada o inactiva")

    # buscamos vínculo existente
    padron_os_attr = _padron_number_attr()
    existing = (await db.execute(
        select(MedicoObraSocial).where(
            and_(MedicoObraSocial.NRO_SOCIO == nro_socio, padron_os_attr == nro_os)
        )
    )).scalar_one_or_none()

    defaults = await _listado_defaults(db, nro_socio)

    if existing:
        if body:
            for k, v in body.model_dump(exclude_unset=True).items():
                if hasattr(existing, k) and v is not None:
                    setattr(existing, k, v)
        # completa campos vacíos desde listado
        for k, v in defaults.items():
            if getattr(existing, k, None) in (None, "", 0):
                setattr(existing, k, v)
        await db.commit()
        await db.refresh(existing)
        return existing

    nuevo = MedicoObraSocial(
        NRO_SOCIO=nro_socio,
        **defaults,
        **{"NRO_OBRASOCIAL": nro_os},
    )
    db.add(nuevo)
    await db.commit()
    await db.refresh(nuevo)
    return nuevo

# 4) DELETE por checkbox: elimina el vínculo usando el NRO_OBRASOCIAL
@router.delete("/{nro_socio}/obras-sociales/{nro_os}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_padron_checkbox(
    nro_socio: int = Path(..., ge=1),
    nro_os: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
):
    padron_os_attr = _padron_number_attr()
    row = (await db.execute(
        select(MedicoObraSocial).where(
            and_(MedicoObraSocial.NRO_SOCIO == nro_socio, padron_os_attr == nro_os)
        )
    )).scalar_one_or_none()
    if not row:
        return
    await db.delete(row)
    await db.commit()
