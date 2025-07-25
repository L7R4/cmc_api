from datetime import date
from dateutil.relativedelta import relativedelta
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ConceptoDescuento, ObraSocial, Medico, Periodo,
    Prestacion, Debito, Descuento
)
from app.schemas.main import (
    ConceptoDescuentoCreate, ConceptoDescuentoUpdate,
    ObraSocialCreate, ObraSocialUpdate,
    MedicoCreate, MedicoUpdate,
    PeriodoCreate, PeriodoUpdate,
    PrestacionCreate, PrestacionUpdate,
    DebitoCreate, DebitoUpdate,
    DescuentoCreate, DescuentoUpdate
)


async def get_medicos(
    db: AsyncSession,
    nombre: str | None = None,
    nro_socio: int | None = None,
    skip: int = 0,
    limit: int = 10,
) -> list[Medico]:
    """
    Lee médicos aplicando filtros opcionales, orden A→Z y paginación.
    """
    query = select(Medico)

    if nombre:
        query = query.where(Medico.nombre.ilike(f"%{nombre}%"))
    if nro_socio is not None:
        query = query.where(Medico.nro_socio == nro_socio)

    # orden alfabético A→Z
    query = query.order_by(Medico.nombre.asc())

    # aplica paginación
    query = query.offset(skip).limit(limit)

    result = await db.execute(query)
    return result.scalars().all()



# ------------------- PERIODOS -------------------

async def get_periodos(db: AsyncSession, skip: int = 0, limit: int = 10):
    q = select(Periodo).offset(skip).limit(limit)
    res = await db.execute(q)
    return res.scalars().all()

async def get_periodo(db: AsyncSession, periodo_id: int):
    res = await db.execute(select(Periodo).where(Periodo.id == periodo_id))
    return res.scalar_one_or_none()

async def get_periodo_by_str(db: AsyncSession, periodo_str: str):
    res = await db.execute(select(Periodo).where(Periodo.periodo == periodo_str))
    return res.scalar_one_or_none()

# async def create_periodo(db: AsyncSession, in_: PeriodoCreate):
#     # 1) creo el periodo solicitado
#     p = Periodo(periodo=in_.periodo)
#     db.add(p)
#     await db.commit()
#     await db.refresh(p)

#     # 2) —— WEPSSS —— auto-creación del siguiente mes
#     dt = date.today().replace(day=1)
#     next_str = (dt + relativedelta(months=1)).strftime("%m/%Y")
#     if not await get_periodo_by_str(db, next_str):
#         await create_periodo(db, PeriodoCreate(periodo=next_str))

#     return p


async def create_periodo(db: AsyncSession, in_: PeriodoCreate) -> Periodo:
    """
    Crea el periodo actual y, si no existe, el siguiente.
    Ambos se almacenan en formato 'YYYYMM'.
    """
    # --- 1) Genero cadena para el periodo actual ---
    hoy = date.today()
    periodo_actual = hoy.strftime("%Y%m")           # e.g. "202507"

    # --- 2) Creo o recupero el registro del periodo actual ---
    existente = await get_periodo_by_str(db, periodo_actual)
    if existente:
        p = existente
    else:
        p = Periodo(periodo=periodo_actual)
        db.add(p)
        await db.commit()
        await db.refresh(p)

    # --- 3) Auto-creación del siguiente mes, también en 'YYYYMM' ---
    primero = hoy.replace(day=1)
    siguiente = (primero + relativedelta(months=1)).strftime("%Y%m")
    if not await get_periodo_by_str(db, siguiente):
        # reutilizo el mismo esquema PeriodoCreate, aunque no se use in_.periodo
        await create_periodo(db, PeriodoCreate(periodo=siguiente))

    return p

async def update_periodo(db: AsyncSession, db_obj: Periodo, in_: PeriodoUpdate):
    # Versionado si reabro
    if (
        db_obj.status == "finalizado"
        and in_.status == "en_curso"
    ):
        db_obj.version += 1

    for field, val in in_.model_dump(exclude_unset=True).items():
        setattr(db_obj, field, val)

    await db.commit()
    await db.refresh(db_obj)
    return db_obj

async def delete_periodo(db: AsyncSession, periodo_id: int):
    await db.execute(delete(Periodo).where(Periodo.id == periodo_id))
    await db.commit()


# ------------------- MEDICOS -------------------

async def get_medicos(db: AsyncSession, skip: int = 0, limit: int = 100):
    res = await db.execute(select(Medico).offset(skip).limit(limit))
    return res.scalars().all()

async def get_medico(db: AsyncSession, med_id: int):
    res = await db.execute(select(Medico).where(Medico.id == med_id))
    return res.scalar_one_or_none()

async def create_medico(db: AsyncSession, in_: MedicoCreate):
    m = Medico(**in_.model_dump())
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m

async def update_medico(db: AsyncSession, db_obj: Medico, in_: MedicoUpdate):
    for f, v in in_.model_dump(exclude_unset=True).items():
        setattr(db_obj, f, v)
    await db.commit()
    await db.refresh(db_obj)
    return db_obj

async def delete_medico(db: AsyncSession, med_id: int):
    await db.execute(delete(Medico).where(Medico.id == med_id))
    await db.commit()


# ------------------- DEBITOS -------------------

async def get_debitos(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    os_id: int | None = None,
    periodo_id: int | None = None,
):
    q = select(Debito)
    if os_id:
        q = q.where(Debito.id_os == os_id)
    if periodo_id:
        q = q.where(Debito.periodo_id == periodo_id)
    q = q.offset(skip).limit(limit)
    res = await db.execute(q)
    return res.scalars().all()

async def get_debito(db: AsyncSession, debito_id: int):
    res = await db.execute(select(Debito).where(Debito.id == debito_id))
    return res.scalar_one_or_none()

async def create_debito(db: AsyncSession, in_: DebitoCreate):
    d = Debito(**in_.model_dump())
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return d

async def update_debito(db: AsyncSession, db_obj: Debito, in_: DebitoUpdate):
    for f, v in in_.model_dump(exclude_unset=True).items():
        setattr(db_obj, f, v)
    await db.commit()
    await db.refresh(db_obj)
    return db_obj

async def delete_debito(db: AsyncSession, debito_id: int):
    await db.execute(delete(Debito).where(Debito.id == debito_id))
    await db.commit()


# ------------------- DESCUENTOS -------------------

async def get_descuentos(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    concepto_id: int | None = None,
    periodo_id: int | None = None,
):
    q = select(Descuento)
    if concepto_id:
        q = q.where(Descuento.id_lista_concepto_descuento == concepto_id)
    if periodo_id:
        q = q.where(Descuento.periodo_id == periodo_id)
    q = q.offset(skip).limit(limit)
    res = await db.execute(q)
    return res.scalars().all()

async def get_descuento(db: AsyncSession, desc_id: int):
    res = await db.execute(select(Descuento).where(Descuento.id == desc_id))
    return res.scalar_one_or_none()

async def create_descuento(db: AsyncSession, in_: DescuentoCreate):
    d = Descuento(**in_.model_dump())
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return d

async def update_descuento(db: AsyncSession, db_obj: Descuento, in_: DescuentoUpdate):
    for f, v in in_.model_dump(exclude_unset=True).items():
        setattr(db_obj, f, v)
    await db.commit()
    await db.refresh(db_obj)
    return db_obj

async def delete_descuento(db: AsyncSession, desc_id: int):
    await db.execute(delete(Descuento).where(Descuento.id == desc_id))
    await db.commit()

async def bulk_create_descuentos(
    db: AsyncSession,
    concepto_id: int,
    periodo_id: int,
    medicos: list[Medico]
):
    created = []
    for m in medicos:
        d = Descuento(
            id_lista_concepto_descuento=concepto_id,
            periodo_id=periodo_id,
            id_med=m.id
        )
        db.add(d)
        created.append(d)
    await db.commit()
    for d in created:
        await db.refresh(d)
    return created


# ------------------- RESÚMENES Y LÓGICA DE LIQUIDACIÓN -------------------

async def compute_liquidacion(
    db: AsyncSession,
    medico_id: int,
    periodo_id: int
):
    """
    Devuelve:
      - bruto: suma de sus prestaciones
      - débitos agrupados por obra social
      - descuentos por concepto
    """
    # 1) total bruto
    bruto = await db.scalar(
        select(func.sum(Prestacion.importe_total))
        .where(Prestacion.id_med == medico_id)
        .where(Prestacion.periodo_id == periodo_id)
    ) or 0

    # 2) débitos por obra
    rows = (await db.execute(
        select(
            Debito.id_os,
            func.sum(Debito.importe).label("total_os")
        )
        .where(Debito.id_med == medico_id)
        .where(Debito.periodo_id == periodo_id)
        .group_by(Debito.id_os)
    )).all()

    debitos_por_os = [
        {"obra_social_id": os_id, "total": total_os}
        for os_id, total_os in rows
    ]

    # 3) descuentos por concepto
    rows2 = (await db.execute(
        select(
            Descuento.id_lista_concepto_descuento,
            func.sum(ConceptoDescuento.precio).label("total_concepto")
        )
        .join(ConceptoDescuento, Descuento.id_lista_concepto_descuento == ConceptoDescuento.id)
        .where(Descuento.id_med == medico_id)
        .where(Descuento.periodo_id == periodo_id)
        .group_by(Descuento.id_lista_concepto_descuento)
    )).all()

    descuentos_por_concepto = [
        {"concepto_id": cid, "total": tot}
        for cid, tot in rows2
    ]

    return {
        "total_bruto": bruto,
        "debitos": debitos_por_os,
        "descuentos": descuentos_por_concepto
    }
