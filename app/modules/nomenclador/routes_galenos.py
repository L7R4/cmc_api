import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models.nomenclador_cmc import Galeno, Valor, ValorComponente
from app.modules.nomenclador import service
from app.modules.nomenclador.schemas import (
    ActualizacionMasivaResult,
    GalenoActualizarPrecioIn,
    GalenoActualizarPrecioMasivoIn,
    GalenoCreate,
    GalenoCrearNivelesIn,
    GalenoOut,
    GalenoUpdate,
)

router = APIRouter()


async def _existe_galeno_vigente(
    db: AsyncSession, obra_social_nro: int, codigo: str, nivel: Optional[int]
) -> bool:
    return await service.buscar_galeno_vigente(db, obra_social_nro, codigo, nivel) is not None


async def _validar_mezcla_niveles(
    db: AsyncSession, obra_social_nro: int, codigo: str, nivel: Optional[int]
) -> None:
    """
    Un galeno es nivelado o no, nunca ambas cosas: no se permite que convivan
    filas vigentes con nivel NULL y con nivel para el mismo (OS, codigo).
    """
    stmt = select(Galeno.nivel).where(
        Galeno.obra_social_nro == obra_social_nro,
        Galeno.codigo == codigo,
        Galeno.vigencia_hasta.is_(None),
        Galeno.activo == True,
        Galeno.nivel.is_not(None) if nivel is None else Galeno.nivel.is_(None),
    ).limit(1)
    conflicto = (await db.execute(stmt)).first()
    if conflicto:
        raise HTTPException(
            409,
            f"El galeno '{codigo}' ya tiene filas vigentes "
            f"{'con nivel' if nivel is None else 'sin nivel'} para OS {obra_social_nro}: "
            f"no se pueden mezclar galenos nivelados y sin nivel",
        )


async def _rotar_precio_galeno(
    db: AsyncSession,
    galeno_anterior: Galeno,
    nuevo_valor_unitario: Decimal,
    vigencia_desde: datetime.date,
) -> Galeno:
    """
    Cierra la vigencia del galeno actual, crea la fila nueva con el precio nuevo
    y regenera el historial de todos los códigos afectados (Regla A).
    No comitea: corre dentro de la transacción del caller.
    """
    if vigencia_desde == galeno_anterior.vigencia_desde:
        galeno_anterior.valor_unitario = nuevo_valor_unitario
        await db.flush()
        await service.regenerar_historial_por_galeno(
            galeno_id_anterior=galeno_anterior.id,
            nuevo_galeno_id=galeno_anterior.id,
            vigencia_desde=vigencia_desde,
            db=db,
        )
        return galeno_anterior

    fecha_corte = vigencia_desde - datetime.timedelta(days=1)
    if galeno_anterior.vigencia_desde > fecha_corte:
        raise HTTPException(
            422,
            f"vigencia_desde ({vigencia_desde}) debe ser posterior a la vigencia "
            f"actual del galeno ({galeno_anterior.vigencia_desde})",
        )

    galeno_anterior.vigencia_hasta = fecha_corte
    galeno_anterior.activo = False
    await db.flush()

    nuevo_galeno = Galeno(
        obra_social_nro=galeno_anterior.obra_social_nro,
        codigo=galeno_anterior.codigo,
        nombre=galeno_anterior.nombre,
        nivel=galeno_anterior.nivel,
        vigencia_desde=vigencia_desde,
        vigencia_hasta=None,
        valor_unitario=nuevo_valor_unitario,
        observacion=galeno_anterior.observacion,
    )
    db.add(nuevo_galeno)
    await db.flush()

    await service.regenerar_historial_por_galeno(
        galeno_id_anterior=galeno_anterior.id,
        nuevo_galeno_id=nuevo_galeno.id,
        vigencia_desde=vigencia_desde,
        db=db,
    )
    return nuevo_galeno


@router.get("/", response_model=List[GalenoOut])
async def list_galenos(
    obra_social_nro: Optional[int] = Query(None),
    codigo: Optional[str] = Query(None),
    nivel: Optional[int] = Query(None),
    vigente_a: Optional[datetime.date] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Galeno)
    if obra_social_nro:
        stmt = stmt.where(Galeno.obra_social_nro == obra_social_nro)
    if codigo:
        stmt = stmt.where(Galeno.codigo == codigo)
    if nivel is not None:
        stmt = stmt.where(Galeno.nivel == nivel)
    if vigente_a:
        stmt = stmt.where(
            Galeno.vigencia_desde <= vigente_a,
            (Galeno.vigencia_hasta.is_(None)) | (Galeno.vigencia_hasta >= vigente_a),
        )
    stmt = stmt.order_by(Galeno.codigo, Galeno.nivel, Galeno.vigencia_desde.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/", response_model=GalenoOut, status_code=201)
async def create_galeno(body: GalenoCreate, db: AsyncSession = Depends(get_db)):
    if await _existe_galeno_vigente(db, body.obra_social_nro, body.codigo, body.nivel):
        raise HTTPException(
            409,
            f"Ya existe un galeno vigente para OS {body.obra_social_nro}, "
            f"codigo '{body.codigo}', nivel {body.nivel} — use actualizar_precio",
        )
    await _validar_mezcla_niveles(db, body.obra_social_nro, body.codigo, body.nivel)
    obj = Galeno(**body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.post("/crear_niveles", response_model=List[GalenoOut], status_code=201)
async def crear_galeno_por_niveles(
    body: GalenoCrearNivelesIn, db: AsyncSession = Depends(get_db)
):
    """
    Alta de un galeno nivelado: crea una fila por nivel con su valor unitario.
    Ej: cirugía adulto con 7 niveles → 7 filas con la misma OS + codigo + vigencia.
    """
    creados: List[Galeno] = []
    for item in body.niveles:
        if await _existe_galeno_vigente(db, body.obra_social_nro, body.codigo, item.nivel):
            raise HTTPException(
                409,
                f"Ya existe un galeno vigente para OS {body.obra_social_nro}, "
                f"codigo '{body.codigo}', nivel {item.nivel}",
            )
        await _validar_mezcla_niveles(db, body.obra_social_nro, body.codigo, item.nivel)
        obj = Galeno(
            obra_social_nro=body.obra_social_nro,
            codigo=body.codigo,
            nombre=body.nombre,
            nivel=item.nivel,
            vigencia_desde=body.vigencia_desde,
            vigencia_hasta=None,
            valor_unitario=item.valor_unitario,
            observacion=body.observacion,
        )
        db.add(obj)
        creados.append(obj)
    await db.commit()
    for obj in creados:
        await db.refresh(obj)
    return creados


@router.get("/historial/{obra_social_nro}/{codigo}", response_model=List[GalenoOut])
async def historial_galeno(
    obra_social_nro: int,
    codigo: str,
    nivel: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Galeno).where(
        Galeno.obra_social_nro == obra_social_nro,
        Galeno.codigo == codigo,
    )
    if nivel is not None:
        stmt = stmt.where(Galeno.nivel == nivel)
    stmt = stmt.order_by(Galeno.nivel, Galeno.vigencia_desde)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{id}", response_model=GalenoOut)
async def get_galeno(id: int, db: AsyncSession = Depends(get_db)):
    obj = await db.get(Galeno, id)
    if not obj:
        raise HTTPException(404, "Galeno no encontrado")
    return obj


@router.put("/{id}", response_model=GalenoOut)
async def update_galeno_metadata(id: int, body: GalenoUpdate, db: AsyncSession = Depends(get_db)):
    obj = await db.get(Galeno, id)
    if not obj:
        raise HTTPException(404, "Galeno no encontrado")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(obj, field, value)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.post("/{id}/actualizar_precio", response_model=GalenoOut)
async def actualizar_precio_galeno(
    id: int, body: GalenoActualizarPrecioIn, db: AsyncSession = Depends(get_db)
):
    """
    Cierra vigencia del galeno actual (esta fila/nivel), crea uno nuevo y regenera
    historial_precio_codigo para todos los códigos que lo usaban. Una transacción.
    """
    galeno_anterior = await db.get(Galeno, id)
    if not galeno_anterior:
        raise HTTPException(404, "Galeno no encontrado")
    if not galeno_anterior.activo:
        raise HTTPException(409, "El galeno no está activo")

    nuevo_galeno = await _rotar_precio_galeno(
        db, galeno_anterior, body.nuevo_valor_unitario, body.vigencia_desde
    )
    await db.commit()
    await db.refresh(nuevo_galeno)
    return nuevo_galeno


@router.post("/actualizar_precio_masivo", response_model=ActualizacionMasivaResult)
async def actualizar_precio_galeno_masivo(
    body: GalenoActualizarPrecioMasivoIn, db: AsyncSession = Depends(get_db)
):
    """
    Actualiza en una sola operación todas las filas vigentes de un galeno
    (p.ej. los 7 niveles de cirugía adulto):
    - `porcentaje`: aplica el mismo % a todos los niveles vigentes.
    - `items`: valor unitario explícito por nivel (los niveles no listados no se tocan).
    """
    stmt = select(Galeno).where(
        Galeno.obra_social_nro == body.obra_social_nro,
        Galeno.codigo == body.codigo,
        Galeno.vigencia_hasta.is_(None),
        Galeno.activo == True,
    )
    vigentes = (await db.execute(stmt)).scalars().all()
    if not vigentes:
        raise HTTPException(404, f"No hay galenos vigentes para '{body.codigo}'")

    por_nivel = {g.nivel: g for g in vigentes}
    actualizados = 0
    errores = []

    if body.porcentaje is not None:
        factor = Decimal("1") + body.porcentaje / Decimal("100")
        objetivos = [
            (g, (g.valor_unitario * factor).quantize(Decimal("0.01"))) for g in vigentes
        ]
    else:
        objetivos = []
        for item in body.items:
            g = por_nivel.get(item.nivel)
            if not g:
                errores.append({"nivel": item.nivel, "motivo": "Sin galeno vigente para ese nivel"})
                continue
            objetivos.append((g, item.nuevo_valor_unitario))

    for galeno, nuevo_vu in objetivos:
        try:
            await _rotar_precio_galeno(db, galeno, nuevo_vu, body.vigencia_desde)
            actualizados += 1
        except HTTPException as e:
            errores.append({"nivel": galeno.nivel, "motivo": e.detail})

    if actualizados == 0 and errores:
        await db.rollback()
        return ActualizacionMasivaResult(actualizados=0, errores=errores)

    await db.commit()
    return ActualizacionMasivaResult(actualizados=actualizados, errores=errores)


@router.delete("/{id}", status_code=204)
async def delete_galeno(id: int, db: AsyncSession = Depends(get_db)):
    obj = await db.get(Galeno, id)
    if not obj:
        raise HTTPException(404, "Galeno no encontrado")
    # Solo bloquea si el galeno está en uso por un valor ACTIVO; los valores
    # cerrados conservan la referencia histórica sin impedir la baja
    stmt = (
        select(ValorComponente.id)
        .join(Valor, Valor.id == ValorComponente.valor_id)
        .where(
            ValorComponente.galeno_id == id,
            ValorComponente.activo == True,
            Valor.estado == "activo",
        )
        .limit(1)
    )
    if (await db.execute(stmt)).first():
        raise HTTPException(409, "El galeno está en uso por valores activos")
    obj.activo = False
    await db.commit()
