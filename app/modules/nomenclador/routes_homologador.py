import csv
import io
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.uploads import leer_texto
from app.db.database import get_db
from app.db.models.nomenclador_cmc import Homologador, NomencladorCMC
from app.modules.nomenclador.schemas import (
    HomologadorCreate,
    HomologadorImportarCSVResult,
    HomologadorOut,
    HomologadorUpdate,
    HomologadorValidarIn,
    HomologadorValidarOut,
    NomencladorOut,
)

router = APIRouter()


@router.get("/", response_model=List[HomologadorOut])
async def list_homologador(
    obra_social_nro: Optional[int] = Query(None),
    q: Optional[str] = Query(None),
    activo: Optional[bool] = Query(True),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Homologador)
    if obra_social_nro:
        stmt = stmt.where(Homologador.obra_social_nro == obra_social_nro)
    if q:
        stmt = stmt.where(
            Homologador.codigo_origen.contains(q) | Homologador.descripcion_origen.contains(q)
        )
    if activo is not None:
        stmt = stmt.where(Homologador.activo == activo)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/", response_model=HomologadorOut, status_code=201)
async def create_homologador(body: HomologadorCreate, db: AsyncSession = Depends(get_db)):
    if not await db.get(NomencladorCMC, body.nomenclador_id):
        raise HTTPException(404, "Código de nomenclador no encontrado")
    # Verificar unicidad (os, codigo_origen) para registros activos
    stmt = select(Homologador).where(
        Homologador.obra_social_nro == body.obra_social_nro,
        Homologador.codigo_origen == body.codigo_origen,
        Homologador.activo == True,
    )
    if (await db.execute(stmt)).scalar_one_or_none():
        raise HTTPException(409, "Ya existe un mapeo activo para ese código origen y obra social")
    obj = Homologador(**body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.get("/{id}", response_model=HomologadorOut)
async def get_homologador(id: int, db: AsyncSession = Depends(get_db)):
    obj = await db.get(Homologador, id)
    if not obj:
        raise HTTPException(404, "Mapeo no encontrado")
    return obj


@router.put("/{id}", response_model=HomologadorOut)
async def update_homologador(id: int, body: HomologadorUpdate, db: AsyncSession = Depends(get_db)):
    obj = await db.get(Homologador, id)
    if not obj:
        raise HTTPException(404, "Mapeo no encontrado")
    if body.nomenclador_id and not await db.get(NomencladorCMC, body.nomenclador_id):
        raise HTTPException(404, "Código de nomenclador no encontrado")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(obj, field, value)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/{id}", status_code=204)
async def delete_homologador(id: int, db: AsyncSession = Depends(get_db)):
    obj = await db.get(Homologador, id)
    if not obj:
        raise HTTPException(404, "Mapeo no encontrado")
    obj.activo = False
    await db.commit()


@router.post("/validar", response_model=HomologadorValidarOut)
async def validar_homologacion(body: HomologadorValidarIn, db: AsyncSession = Depends(get_db)):
    stmt = select(Homologador).where(
        Homologador.obra_social_nro == body.obra_social_nro,
        Homologador.codigo_origen == body.codigo_origen,
        Homologador.activo == True,
    )
    obj = (await db.execute(stmt)).scalar_one_or_none()
    if not obj:
        raise HTTPException(
            404,
            f"Código '{body.codigo_origen}' no homologado para OS {body.obra_social_nro}",
        )
    nom = await db.get(NomencladorCMC, obj.nomenclador_id)
    return HomologadorValidarOut(
        nomenclador=NomencladorOut.model_validate(nom),
        homologador_id=obj.id,
    )


@router.post("/importar_csv", response_model=HomologadorImportarCSVResult)
async def importar_homologaciones_csv(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    CSV esperado: obra_social_nro,codigo_origen,codigo_colegio,descripcion_origen
    """
    texto = await leer_texto(file)
    reader = csv.DictReader(io.StringIO(texto))

    procesados = 0
    errores = []

    for i, row in enumerate(reader, start=2):
        try:
            obra_social_nro = int(row["obra_social_nro"])
            codigo_origen = row["codigo_origen"].strip()
            codigo_colegio = row["codigo_colegio"].strip()
            descripcion_origen = row.get("descripcion_origen", "").strip() or None

            # Buscar el nomenclador por codigo
            stmt = select(NomencladorCMC).where(
                NomencladorCMC.codigo == codigo_colegio,
                NomencladorCMC.activo == True,
            )
            nom = (await db.execute(stmt)).scalar_one_or_none()
            if not nom:
                errores.append({"fila": i, "motivo": f"Código CMC '{codigo_colegio}' no encontrado"})
                continue

            # Upsert: si existe lo actualiza, si no existe lo crea
            stmt_exist = select(Homologador).where(
                Homologador.obra_social_nro == obra_social_nro,
                Homologador.codigo_origen == codigo_origen,
            )
            existing = (await db.execute(stmt_exist)).scalar_one_or_none()
            if existing:
                existing.nomenclador_id = nom.id
                existing.descripcion_origen = descripcion_origen
                existing.activo = True
            else:
                db.add(Homologador(
                    obra_social_nro=obra_social_nro,
                    codigo_origen=codigo_origen,
                    nomenclador_id=nom.id,
                    descripcion_origen=descripcion_origen,
                ))
            procesados += 1
        except Exception as e:
            errores.append({"fila": i, "motivo": str(e)})

    await db.commit()
    return HomologadorImportarCSVResult(procesados=procesados, errores=errores)
