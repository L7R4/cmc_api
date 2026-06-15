import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.database import get_db
from app.db.models import ListadoMedico, NomencladorCMC, ObrasSociales
from app.modules.facturacion import service
from app.modules.facturacion.schemas import (
    AfiliadoCreate,
    AfiliadoRead,
    GuardadoResponse,
    MoverPeriodoPayload,
    MoverPeriodoResponse,
    PeriodoActivoResponse,
    PrecioResponse,
    PrestacionesCreate,
    PrestacionRead,
    PrestacionUpdate,
)

router = APIRouter()


def _usuario(user: dict) -> str:
    return str(user["nro_socio"])


# ── Grupo A — Autocomplete ───────────────────────────────────────────────────
@router.get("/medicos")
async def buscar_medicos(
    q: str = Query(..., min_length=2),
    limit: int = Query(20, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    M = ListadoMedico
    cond = M.NOMBRE.ilike(f"%{q}%")
    if q.isdigit():
        cond = or_(M.NRO_SOCIO == int(q), cond)
    rows = (await db.execute(select(M).where(cond).limit(limit))).scalars().all()
    return [
        {"cod": str(m.NRO_SOCIO), "nombre": m.NOMBRE,
         "matricula": m.MATRICULA_PROV, "categoria": m.CATEGORIA}
        for m in rows
    ]


@router.get("/obras-sociales")
async def buscar_obras_sociales(
    q: str = Query(..., min_length=2),
    limit: int = Query(20, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    O = ObrasSociales
    rows = (
        await db.execute(select(O).where(O.OBRA_SOCIAL.ilike(f"%{q}%")).limit(limit))
    ).scalars().all()
    return [
        {"id": o.ID, "nro_obra_social": o.NRO_OBRASOCIAL, "nombre": o.OBRA_SOCIAL}
        for o in rows
    ]


@router.get("/nomenclador")
async def buscar_nomenclador(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    N = NomencladorCMC
    rows = (
        await db.execute(
            select(N).where(
                N.activo.is_(True),
                or_(N.codigo.ilike(f"{q}%"), N.descripcion.ilike(f"%{q}%")),
            ).limit(limit)
        )
    ).scalars().all()
    return [{"codigo": n.codigo, "descripcion": n.descripcion} for n in rows]


# ── Grupo A2 — Afiliados ─────────────────────────────────────────────────────
@router.get("/afiliados/{dni}", response_model=AfiliadoRead)
async def get_afiliado(dni: str, db: AsyncSession = Depends(get_db)):
    afiliado = await service.get_afiliado_by_dni(db, dni)
    if not afiliado:
        raise HTTPException(404, f"Afiliado con DNI {dni} no encontrado")
    return afiliado


@router.post("/afiliados", response_model=AfiliadoRead, status_code=status.HTTP_201_CREATED)
async def crear_afiliado(
    payload: AfiliadoCreate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await service.crear_afiliado(db, payload, _usuario(user))


# ── Grupo B — Período y precio ───────────────────────────────────────────────
@router.get("/periodo-activo", response_model=PeriodoActivoResponse)
async def periodo_activo(
    cod_obra: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    periodo = await service.get_periodo_activo(db, cod_obra)
    return PeriodoActivoResponse(
        cod_obra=cod_obra, periodo=periodo, periodo_label=service.periodo_label(periodo)
    )


@router.get("/nomenclador/precio", response_model=PrecioResponse)
async def precio_nomenclador(
    cod_medico: str = Query(...),
    cod_obra: str = Query(...),
    codigo: str = Query(...),
    fecha: Optional[datetime.date] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    medico = await service.check_medico_activo(db, cod_medico)
    if fecha is None:
        periodo = await service.get_periodo_activo(db, cod_obra)
        fecha = service.normalizar_fecha_practica(None, periodo)
    return await service.resolver_precio(db, cod_obra, medico, codigo, fecha)


# ── Grupo C — Prestaciones ───────────────────────────────────────────────────
@router.get("/prestaciones/recientes", response_model=list[PrestacionRead])
async def prestaciones_recientes(
    cod_obra: str = Query(...),
    usuario: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    return await service.prestaciones_recientes(db, cod_obra, usuario)


@router.get("/prestaciones", response_model=list[PrestacionRead])
async def listar_prestaciones(
    response: Response,
    cod_obra: Optional[str] = Query(None),
    periodo: Optional[str] = Query(None),
    cod_medico: Optional[str] = Query(None),
    cod_nomenclador: Optional[str] = Query(None),
    nro_orden: Optional[str] = Query(None),
    estado: Optional[str] = Query(None),
    tpo_funcion: Optional[str] = Query(None),
    tpo_servicio: Optional[str] = Query(None),
    tipo_orden: Optional[str] = Query(None),
    dni_paciente: Optional[str] = Query(None),
    nombre_paciente: Optional[str] = Query(None),
    fecha_desde: Optional[datetime.date] = Query(None),
    fecha_hasta: Optional[datetime.date] = Query(None),
    q: Optional[str] = Query(None, description="Búsqueda libre: médico, código, paciente, nro_orden"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    rows, total = await service.listar_prestaciones(
        db,
        cod_obra=cod_obra, periodo=periodo, cod_medico=cod_medico,
        cod_nomenclador=cod_nomenclador, nro_orden=nro_orden, estado=estado,
        tpo_funcion=tpo_funcion, tpo_servicio=tpo_servicio, tipo_orden=tipo_orden,
        dni_paciente=dni_paciente, nombre_paciente=nombre_paciente,
        fecha_desde=fecha_desde, fecha_hasta=fecha_hasta, q=q,
        limit=limit, offset=offset,
    )
    response.headers["X-Total-Count"] = str(total)
    response.headers["Content-Range"] = f"prestaciones {offset}-{offset + len(rows)}/{total}"
    return rows


@router.post("/prestaciones", response_model=GuardadoResponse, status_code=status.HTTP_201_CREATED)
async def crear_prestaciones(
    payload: PrestacionesCreate,
    confirmar_duplicado: bool = Query(False),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await service.guardar_prestaciones(db, payload, _usuario(user), confirmar_duplicado)


@router.post("/prestaciones/mover-periodo", response_model=MoverPeriodoResponse)
async def mover_periodo(
    payload: MoverPeriodoPayload,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await service.mover_prestaciones_periodo(db, payload)


@router.patch("/prestaciones/{id}", response_model=PrestacionRead)
async def editar_prestacion(
    id: int,
    payload: PrestacionUpdate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await service.editar_prestacion(db, id, payload)


@router.delete("/prestaciones/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def anular_prestacion(
    id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.anular_prestacion(db, id, _usuario(user))
