from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from datetime import datetime

from app.db.database import get_db
from app.db.models import GuardarAtencion, Debito_Credito   # ajustá import si difiere
from app.schemas.debitos_creditos_schema import (
    DebitoCreditoCreate, DebitoCreditoUpdate, DebitoCreditoOut
)
from app.utils.main import normalizar_periodo

router = APIRouter()

# ---------------------------
# 1) Prestaciones por OS + periodo (según guardar_atencion)
# ---------------------------
# @router.get("/{obra_social_id}/{periodo_id}")
# async def prestaciones_por_os_y_periodo(
#     obra_social_id: int = Path(..., description="Código de obra social"),
#     periodo_id: str = Path(..., description="YYYY-MM o YYYYMM"),
#     limit: int = Query(5000, ge=1, le=20000),
#     db: AsyncSession = Depends(get_db),
# ) -> List[Dict[str, Any]]:
#     try:
#         anio, mes, _ = normalizar_periodo(periodo_id)
#     except ValueError as e:
#         raise HTTPException(400, str(e))

#     stmt = (
#         select(
#             GuardarAtencion.ID.label("id_atencion"),
#             GuardarAtencion.NRO_SOCIO.label("medico_id"),
#             GuardarAtencion.NOMBRE_PRESTADOR.label("medico_nombre"),
#             GuardarAtencion.NRO_OBRA_SOCIAL.label("obra_social_id"),
#             GuardarAtencion.CODIGO_PRESTACION.label("codigo_prestacion"),
#             GuardarAtencion.FECHA_PRESTACION.label("fecha_prestacion"),
#             GuardarAtencion.VALOR_CIRUJIA.label("valor_cirugia"),
#             GuardarAtencion.VALOR_AYUDANTE.label("valor_ayudante"),
#             GuardarAtencion.VALOR_AYUDANTE_2.label("valor_ayudante_2"),
#             GuardarAtencion.GASTOS.label("gastos"),
#             GuardarAtencion.CANTIDAD.label("cantidad"),
#             GuardarAtencion.CANT_TRATAMIENTO.label("cantidad_tratamiento"),
#         )
#         .where(
#             and_(
#                 GuardarAtencion.NRO_OBRA_SOCIAL == obra_social_id,
#                 GuardarAtencion.ANIO_PERIODO == anio,
#                 GuardarAtencion.MES_PERIODO == mes,
#             )
#         )
#         .limit(limit)
#     )
#     rows = (await db.execute(stmt)).mappings().all()
#     return [dict(r) for r in rows]

# ---------------------------
# 2) Crear débito/crédito
# ---------------------------
@router.post("", response_model=DebitoCreditoOut, status_code=201)
async def crear_debito_credito(
    payload: DebitoCreditoCreate,
    db: AsyncSession = Depends(get_db),
):
    # Normalizar período
    _, _, periodo_norm = normalizar_periodo(payload.periodo)

    # Verificar que la atención exista y coincida en OS y periodo
    ga = await db.execute(
        select(
            GuardarAtencion.ID,
            GuardarAtencion.NRO_OBRA_SOCIAL,
            GuardarAtencion.ANIO_PERIODO,
            GuardarAtencion.MES_PERIODO,
        ).where(GuardarAtencion.ID == payload.id_atencion)
    )
    ga_row = ga.first()
    if not ga_row:
        raise HTTPException(404, "id_atencion inexistente")

    periodo_ga = f"{ga_row.ANIO_PERIODO:04d}-{ga_row.MES_PERIODO:02d}"
    if ga_row.NRO_OBRA_SOCIAL != payload.obra_social_id:
        raise HTTPException(400, "obra_social_id no coincide con la atención")
    if periodo_ga != periodo_norm:
        raise HTTPException(400, f"periodo '{periodo_norm}' no coincide con la atención ({periodo_ga})")

    obj = Debito_Credito(
        tipo=payload.tipo,
        id_atencion=payload.id_atencion,
        obra_social_id=payload.obra_social_id,
        periodo=periodo_norm,
        monto=payload.monto,
        observacion=payload.observacion,
        creado_timestamp=datetime.utcnow().isoformat(timespec="seconds"),
        created_by_user=payload.created_by_user,
    )
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj

# ---------------------------
# 3) Editar débito/crédito
# ---------------------------
@router.put("/{debcre_id}", response_model=DebitoCreditoOut)
async def editar_debito_credito(
    debcre_id: int,
    payload: DebitoCreditoUpdate,
    db: AsyncSession = Depends(get_db),
):
    obj = await db.get(Debito_Credito, debcre_id)
    if not obj:
        raise HTTPException(404, "No encontrado")

    data = payload.dict(exclude_unset=True)

    # Si se toca cualquiera de estos campos, volver a validar contra la atención
    id_atencion_nuevo = data.get("id_atencion", obj.id_atencion)
    obra_social_id_nuevo = data.get("obra_social_id", obj.obra_social_id)
    periodo_nuevo = data.get("periodo", obj.periodo)

    _, _, periodo_norm = normalizar_periodo(periodo_nuevo)

    ga = await db.execute(
        select(
            GuardarAtencion.ID,
            GuardarAtencion.NRO_OBRA_SOCIAL,
            GuardarAtencion.ANIO_PERIODO,
            GuardarAtencion.MES_PERIODO,
        ).where(GuardarAtencion.ID == id_atencion_nuevo)
    )
    ga_row = ga.first()
    if not ga_row:
        raise HTTPException(404, "id_atencion inexistente")

    periodo_ga = f"{ga_row.ANIO_PERIODO:04d}-{ga_row.MES_PERIODO:02d}"
    if ga_row.NRO_OBRA_SOCIAL != obra_social_id_nuevo:
        raise HTTPException(400, "obra_social_id no coincide con la atención")
    if periodo_ga != periodo_norm:
        raise HTTPException(400, f"periodo '{periodo_norm}' no coincide con la atención ({periodo_ga})")

    # Aplicar cambios
    for k, v in data.items():
        if k == "periodo":
            setattr(obj, k, periodo_norm)
        else:
            setattr(obj, k, v)

    await db.commit()
    await db.refresh(obj)
    return obj

# ---------------------------
# 4) Eliminar débito/crédito
# ---------------------------
@router.delete("/{debcre_id}", status_code=204)
async def borrar_debito_credito(
    debcre_id: int,
    db: AsyncSession = Depends(get_db),
):
    obj = await db.get(Debito_Credito, debcre_id)
    if not obj:
        raise HTTPException(404, "No encontrado")
    await db.delete(obj)
    await db.commit()
    return None

# ---------------------------
# 5) (Opcional) Listar filtrando (sin colisionar con GET prestaciones)
# ---------------------------
@router.get("", response_model=List[DebitoCreditoOut])
async def listar_debitos_creditos(
    obra_social_id: Optional[int] = Query(None),
    periodo: Optional[str] = Query(None, description="YYYY-MM/ YYYYMM"),
    tipo: Optional[str] = Query(None, regex="^(d|c)$"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Debito_Credito)
    if obra_social_id is not None:
        stmt = stmt.where(Debito_Credito.obra_social_id == obra_social_id)
    if periodo:
        _, _, pnorm = normalizar_periodo(periodo)
        stmt = stmt.where(Debito_Credito.periodo == pnorm)
    if tipo:
        stmt = stmt.where(Debito_Credito.tipo == tipo)
    stmt = stmt.offset(skip).limit(limit)
    res = await db.execute(stmt)
    return res.scalars().all()