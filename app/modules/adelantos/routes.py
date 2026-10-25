"""
Adelantos: prestaciones de UNA factura cerrada que se pagan antes que el resto.

Se manejan como un lote de débitos y créditos:
  A  → C   cerrar (ya no se editan las prestaciones)
  C  → A   reabrir
  C  → L   pasar al pago abierto (asigna `pago_id`)
  L  → C   quitar del pago abierto
  L  → AP  lo hace el cierre del pago      (ver pagos/routes.py)
  AP → L   lo hace la reapertura del pago
  AP → C   lo hace la eliminación del pago

No crea `liquidacion` ni `detalle_liquidacion`: sus importes suman a la producción del
médico en el pago por `pago_id` (ver `queries.py`). Una prestación reservada en un adelanto
(en cualquier estado) queda afuera de la factura cuando ésta entra a un pago.
"""
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, and_, cast, exists, func, not_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import (
    DetalleFacturacionCMC,
    DetalleLiquidacion,
    FacturacionCMC,
    Liquidacion,
    LiquidacionAdelanto,
    LiquidacionAdelantoItem,
    ListadoMedico,
    ObrasSociales,
    Pago,
)
from app.db.models.nomenclador_cmc import NomencladorCMC
from app.modules.adelantos.schemas import (
    AdelantoCreate,
    AdelantoEstadoPayload,
    AdelantoItemRead,
    AdelantoItemsAdd,
    AdelantoItemsResultado,
    AdelantoListaRow,
    AdelantoRead,
    AdelantoUpdate,
    FacturaParaAdelanto,
    PrestacionesFacturaPage,
    PrestacionFacturaRow,
)
from app.modules.deducciones.service import generar_y_recalcular_porcentuales
from app.modules.liquidacion.service import repartir_honorarios_gastos, to_dec

router = APIRouter()

D = DetalleFacturacionCMC
FACTURA_CERRADA = ("C", "L", "LC")

_FUNCION_ROTULO = {
    "H": "Médico", "P": "Médico", "HG": "Médico y gastos", "G": "Gastos", "A": "Ayudante",
}


def _rotulo_funcion(tpo: Optional[str]) -> str:
    return _FUNCION_ROTULO.get((tpo or "H").upper(), tpo or "Médico")


# ================================================
# Condiciones reutilizables sobre detalle_facturacion
# ================================================
def _en_algun_adelanto():
    return exists().where(LiquidacionAdelantoItem.detalle_facturacion_id == D.id_detalle_prestaciones).correlate(D)


def _en_alguna_liquidacion():
    return exists().where(DetalleLiquidacion.cmc_detalle_id == D.id_detalle_prestaciones).correlate(D)


def _de_la_factura(factura: FacturacionCMC) -> list:
    """Las prestaciones de ESTA factura: obra social, período y versión (original o complementaria)."""
    return [
        D.cod_obr == str(factura.cod_obr),
        D.periodo == str(factura.periodo),
        D.version == factura.version,
    ]


# ================================================
# Lectura de un adelanto
# ================================================
async def _factura_de(db: AsyncSession, adelanto: LiquidacionAdelanto) -> Optional[FacturacionCMC]:
    return await db.get(FacturacionCMC, adelanto.factura_id)


async def _nombre_os(db: AsyncSession, obra_social_id: int) -> str:
    return (await db.execute(
        select(ObrasSociales.OBRA_SOCIAL).where(ObrasSociales.NRO_OBRASOCIAL == obra_social_id)
    )).scalar_one_or_none() or ""


async def _pago_abierto_id(db: AsyncSession) -> Optional[int]:
    return (await db.execute(select(Pago.id).where(Pago.estado == "A").limit(1))).scalar_one_or_none()


async def _leer(db: AsyncSession, adelanto: LiquidacionAdelanto) -> AdelantoRead:
    factura = await _factura_de(db, adelanto)
    filas = (await db.execute(
        select(
            LiquidacionAdelantoItem,
            D.fecha_practica, D.cod_nom, D.tpo_funcion,
            NomencladorCMC.descripcion.label("descripcion"),
            ListadoMedico.NRO_SOCIO.label("nro_socio"),
            ListadoMedico.NOMBRE.label("medico_nombre"),
        )
        .select_from(LiquidacionAdelantoItem)
        .outerjoin(D, D.id_detalle_prestaciones == LiquidacionAdelantoItem.detalle_facturacion_id)
        .outerjoin(NomencladorCMC, NomencladorCMC.id == D.nomenclador_id)
        .outerjoin(ListadoMedico, ListadoMedico.ID == LiquidacionAdelantoItem.medico_id)
        .where(LiquidacionAdelantoItem.adelanto_id == adelanto.id)
        .order_by(LiquidacionAdelantoItem.id)
    )).all()

    items = [
        AdelantoItemRead(
            id=r[0].id,
            detalle_id=int(r[0].detalle_facturacion_id),
            fecha_practica=r.fecha_practica,
            codigo=str(r.cod_nom) if r.cod_nom is not None else None,
            descripcion=r.descripcion,
            medico_id=r[0].medico_id,
            medico_nro_socio=r.nro_socio,
            medico_nombre=(r.medico_nombre or "").strip() or None,
            funcion=_rotulo_funcion(r.tpo_funcion),
            honorarios=r[0].honorarios,
            gastos=r[0].gastos,
            importe_total=r[0].importe_total,
        )
        for r in filas
    ]
    return AdelantoRead(
        id=adelanto.id,
        estado=adelanto.estado,
        pago_id=adelanto.pago_id,
        factura_id=adelanto.factura_id,
        nro_factura=factura.nro_factura if factura else None,
        factura_version=factura.version if factura else None,
        obra_social_id=adelanto.obra_social_id,
        obra_social_nombre=await _nombre_os(db, adelanto.obra_social_id),
        mes_periodo=adelanto.mes_periodo,
        anio_periodo=adelanto.anio_periodo,
        observacion=adelanto.observacion,
        created_at=adelanto.created_at,
        medicos=len({i.medico_id for i in items}),
        prestaciones=len(items),
        importe=sum((i.importe_total for i in items), Decimal("0")),
        items=items,
        pago_abierto_id=await _pago_abierto_id(db),
    )


async def _get_adelanto(db: AsyncSession, adelanto_id: int) -> LiquidacionAdelanto:
    adelanto = await db.get(LiquidacionAdelanto, adelanto_id)
    if not adelanto:
        raise HTTPException(404, "Adelanto no encontrado")
    return adelanto


# ================================================
# GET /facturas_disponibles — facturas cerradas con prestaciones para adelantar
# ================================================
@router.get("/facturas_disponibles", response_model=List[FacturaParaAdelanto])
async def facturas_disponibles(
    obra_social_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Facturas cerradas de la obra social con prestaciones que todavía se pueden adelantar.

    Disponible = cerrada ('C'), con importe, sin pagar, ni en un pago, ni en otro adelanto.
    `RESERVADAS` son las que ya están en algún adelanto, para avisarlo al elegir la factura.
    """
    cod_obr = str(obra_social_id)
    libre = and_(
        D.estado == "C",
        D.importe_total > 0,
        not_(_en_algun_adelanto()),
        not_(_en_alguna_liquidacion()),
    )
    resumen = (
        select(
            D.periodo.label("periodo"),
            D.version.label("version"),
            func.sum(func.IF(libre, 1, 0)).label("disponibles"),
            func.coalesce(func.sum(func.IF(libre, D.importe_total, 0)), 0).label("importe"),
            func.count(func.distinct(func.IF(libre, D.cod_med, None))).label("medicos"),
            func.sum(func.IF(and_(D.estado == "C", _en_algun_adelanto()), 1, 0)).label("reservadas"),
        )
        .where(D.cod_obr == cod_obr, D.estado.in_(["C", "L"]))
        .group_by(D.periodo, D.version)
        .subquery()
    )
    rows = (await db.execute(
        select(
            FacturacionCMC.id_prestaciones, FacturacionCMC.periodo, FacturacionCMC.nro_factura,
            FacturacionCMC.tipo_factura, FacturacionCMC.version,
            resumen.c.disponibles, resumen.c.importe, resumen.c.medicos, resumen.c.reservadas,
        )
        .join(
            resumen,
            and_(resumen.c.periodo == FacturacionCMC.periodo, resumen.c.version == FacturacionCMC.version),
        )
        .where(FacturacionCMC.cod_obr == cod_obr, FacturacionCMC.estado.in_(FACTURA_CERRADA))
        .order_by(FacturacionCMC.periodo.desc(), FacturacionCMC.version)
    )).mappings().all()
    return [
        FacturaParaAdelanto(
            FACTURA_ID=r["id_prestaciones"],
            ANIO=int(r["periodo"][:4]),
            MES=int(r["periodo"][4:]),
            NRO_FACTURA=r["nro_factura"],
            TIPO_FACTURA=r["tipo_factura"],
            VERSION=r["version"],
            PERIODO=r["periodo"],
            MEDICOS=int(r["medicos"] or 0),
            DISPONIBLES=int(r["disponibles"] or 0),
            IMPORTE_DISPONIBLE=Decimal(str(r["importe"] or 0)),
            RESERVADAS=int(r["reservadas"] or 0),
        )
        for r in rows
        if int(r["disponibles"] or 0) > 0 or int(r["reservadas"] or 0) > 0
    ]


# ================================================
# GET /lista — listado enriquecido
# ================================================
@router.get("/lista", response_model=List[AdelantoListaRow])
async def listar_adelantos(
    obra_social_id: Optional[int] = Query(None),
    mes: Optional[int] = Query(None, ge=1, le=12),
    anio: Optional[int] = Query(None, ge=1900),
    estado: Optional[str] = Query(None, description="A | C | L | AP"),
    medico_id: Optional[int] = Query(None, description="listado_medico.ID de alguien que cobra en el adelanto"),
    pago_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    A, I = LiquidacionAdelanto, LiquidacionAdelantoItem
    stmt = (
        select(
            A.id, A.estado, A.pago_id, A.factura_id, A.obra_social_id, A.mes_periodo, A.anio_periodo,
            A.observacion, A.created_at,
            ObrasSociales.OBRA_SOCIAL.label("os_nombre"),
            FacturacionCMC.nro_factura, FacturacionCMC.version.label("factura_version"),
            func.count(I.id).label("prestaciones"),
            func.coalesce(func.sum(I.importe_total), 0).label("importe"),
            func.count(func.distinct(I.medico_id)).label("medicos"),
            func.min(ListadoMedico.NOMBRE).label("medico_nombre"),
            func.min(ListadoMedico.NRO_SOCIO).label("medico_nro_socio"),
        )
        .select_from(A)
        .join(ObrasSociales, ObrasSociales.NRO_OBRASOCIAL == A.obra_social_id)
        .outerjoin(FacturacionCMC, FacturacionCMC.id_prestaciones == A.factura_id)
        .outerjoin(I, I.adelanto_id == A.id)
        .outerjoin(ListadoMedico, ListadoMedico.ID == I.medico_id)
        .group_by(
            A.id, A.estado, A.pago_id, A.factura_id, A.obra_social_id, A.mes_periodo, A.anio_periodo,
            A.observacion, A.created_at, ObrasSociales.OBRA_SOCIAL,
            FacturacionCMC.nro_factura, FacturacionCMC.version,
        )
        .order_by(A.anio_periodo.desc(), A.mes_periodo.desc(), A.id.desc())
    )
    if obra_social_id is not None:
        stmt = stmt.where(A.obra_social_id == obra_social_id)
    if mes is not None:
        stmt = stmt.where(A.mes_periodo == mes)
    if anio is not None:
        stmt = stmt.where(A.anio_periodo == anio)
    if estado:
        stmt = stmt.where(A.estado == estado)
    if pago_id is not None:
        stmt = stmt.where(A.pago_id == pago_id)
    if medico_id is not None:
        stmt = stmt.where(exists().where(I.adelanto_id == A.id, I.medico_id == medico_id))

    rows = (await db.execute(stmt)).mappings().all()
    return [
        AdelantoListaRow(
            id=r["id"], estado=r["estado"], pago_id=r["pago_id"], factura_id=r["factura_id"],
            nro_factura=r["nro_factura"], factura_version=r["factura_version"],
            obra_social_id=r["obra_social_id"], obra_social_nombre=r["os_nombre"] or "",
            mes_periodo=r["mes_periodo"], anio_periodo=r["anio_periodo"],
            medico_nombre=(r["medico_nombre"] or "").strip() or None,
            medico_nro_socio=r["medico_nro_socio"],
            medicos=int(r["medicos"] or 0), prestaciones=int(r["prestaciones"] or 0),
            importe=Decimal(str(r["importe"] or 0)),
            observacion=r["observacion"], created_at=r["created_at"],
        )
        for r in rows
    ]


# ================================================
# POST / — crear un adelanto (vacío) de una factura cerrada
# ================================================
@router.post("/", response_model=AdelantoRead, status_code=201)
async def crear_adelanto(payload: AdelantoCreate, db: AsyncSession = Depends(get_db)):
    factura = await db.get(FacturacionCMC, payload.factura_id)
    if not factura:
        raise HTTPException(404, "La factura no existe")
    if factura.estado not in FACTURA_CERRADA:
        raise HTTPException(409, "La factura todavía no está cerrada")
    periodo = str(factura.periodo or "")
    if len(periodo) != 6 or not periodo.isdigit():
        raise HTTPException(422, f"La factura tiene un período inválido: '{periodo}'")

    adelanto = LiquidacionAdelanto(
        factura_id=factura.id_prestaciones,
        obra_social_id=int(factura.cod_obr),
        mes_periodo=int(periodo[4:]),
        anio_periodo=int(periodo[:4]),
        estado="A",
        observacion=(payload.observacion or "").strip() or None,
    )
    db.add(adelanto)
    await db.commit()
    await db.refresh(adelanto)
    return await _leer(db, adelanto)


# ================================================
# GET /{id} · PATCH /{id} · DELETE /{id}
# ================================================
@router.get("/{adelanto_id}", response_model=AdelantoRead)
async def obtener_adelanto(adelanto_id: int, db: AsyncSession = Depends(get_db)):
    return await _leer(db, await _get_adelanto(db, adelanto_id))


@router.patch("/{adelanto_id}", response_model=AdelantoRead)
async def actualizar_adelanto(
    adelanto_id: int, payload: AdelantoUpdate, db: AsyncSession = Depends(get_db)
):
    adelanto = await _get_adelanto(db, adelanto_id)
    if adelanto.estado in ("L", "AP"):
        raise HTTPException(409, "El adelanto está en un pago y no se puede editar")
    adelanto.observacion = (payload.observacion or "").strip() or None
    await db.commit()
    await db.refresh(adelanto)
    return await _leer(db, adelanto)


@router.delete("/{adelanto_id}", status_code=204)
async def eliminar_adelanto(adelanto_id: int, db: AsyncSession = Depends(get_db)):
    """Solo con el adelanto abierto, como los lotes. Libera sus prestaciones."""
    adelanto = await _get_adelanto(db, adelanto_id)
    if adelanto.estado != "A":
        raise HTTPException(
            409,
            "Solo se puede eliminar un adelanto abierto. "
            "Reabrilo primero o quitalo del pago." if adelanto.estado in ("C", "L")
            else "Un adelanto pagado no se puede eliminar: pertenece a un pago cerrado.",
        )
    await db.delete(adelanto)
    await db.commit()
    return None


# ================================================
# GET /{id}/prestaciones — prestaciones de la factura y su estado frente a los adelantos
# ================================================
@router.get("/{adelanto_id}/prestaciones", response_model=PrestacionesFacturaPage)
async def prestaciones_de_la_factura(
    adelanto_id: int,
    q: Optional[str] = Query(None, description="N° de prestación, orden, código, socio, médico o afiliado"),
    medico_id: Optional[int] = Query(None, description="listado_medico.ID de quien cobra la fila"),
    solo_disponibles: bool = Query(False),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    adelanto = await _get_adelanto(db, adelanto_id)
    factura = await _factura_de(db, adelanto)
    if not factura:
        raise HTTPException(404, "La factura del adelanto ya no existe")

    base = [*_de_la_factura(factura), D.estado.in_(["C", "L"])]
    libre = and_(
        D.estado == "C", D.importe_total > 0,
        not_(_en_algun_adelanto()), not_(_en_alguna_liquidacion()),
    )
    medico_de_la_fila = cast(D.cod_med, String)

    stmt = (
        select(
            D,
            NomencladorCMC.descripcion.label("descripcion"),
            ListadoMedico.ID.label("medico_id"),
            ListadoMedico.NRO_SOCIO.label("nro_socio"),
            ListadoMedico.NOMBRE.label("medico_nombre"),
        )
        .outerjoin(NomencladorCMC, NomencladorCMC.id == D.nomenclador_id)
        .outerjoin(ListadoMedico, ListadoMedico.NRO_SOCIO == medico_de_la_fila)
        .where(*base)
    )
    texto = (q or "").strip()
    if texto.isdigit():
        stmt = stmt.where(or_(
            D.id_detalle_prestaciones == int(texto),
            D.nro_orden == texto,
            D.cod_med == texto,
            D.cod_nom.like(f"{texto}%"),
            D.dni_p == texto,
        ))
    elif texto:
        term = f"%{texto}%"
        stmt = stmt.where(or_(
            ListadoMedico.NOMBRE.like(term), D.nom_ape_p.like(term), D.cod_nom.like(term),
        ))
    if medico_id is not None:
        stmt = stmt.where(ListadoMedico.ID == medico_id)
    if solo_disponibles:
        stmt = stmt.where(libre, ListadoMedico.ID.isnot(None))

    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (await db.execute(
        stmt.order_by(D.fecha_practica, D.id_detalle_prestaciones).limit(size).offset((page - 1) * size)
    )).all()

    ids = [r[0].id_detalle_prestaciones for r in rows]
    # Dónde está cada prestación de la página: en qué adelanto y en qué pago.
    en_adelanto: dict[int, tuple[int, str]] = {}
    en_liq: dict[int, int] = {}
    if ids:
        for det_id, adel_id, adel_estado in (await db.execute(
            select(
                LiquidacionAdelantoItem.detalle_facturacion_id,
                LiquidacionAdelanto.id, LiquidacionAdelanto.estado,
            )
            .join(LiquidacionAdelanto, LiquidacionAdelanto.id == LiquidacionAdelantoItem.adelanto_id)
            .where(LiquidacionAdelantoItem.detalle_facturacion_id.in_(ids))
        )).all():
            en_adelanto[int(det_id)] = (int(adel_id), adel_estado)
        for det_id, pago_id in (await db.execute(
            select(DetalleLiquidacion.cmc_detalle_id, Liquidacion.pago_id)
            .join(Liquidacion, Liquidacion.id == DetalleLiquidacion.liquidacion_id)
            .where(DetalleLiquidacion.cmc_detalle_id.in_(ids))
        )).all():
            en_liq[int(det_id)] = int(pago_id)

    # Equipo: la fila "cabeza" (grupo_equipo_id == su id) y quienes cuelgan de ella.
    grupos = {r[0].grupo_equipo_id for r in rows if r[0].grupo_equipo_id}
    integrantes: dict[int, int] = {}
    if grupos:
        integrantes = {
            int(g): int(n) for g, n in (await db.execute(
                select(D.grupo_equipo_id, func.count()).where(D.grupo_equipo_id.in_(grupos)).group_by(D.grupo_equipo_id)
            )).all()
        }

    items: list[PrestacionFacturaRow] = []
    for r in rows:
        d = r[0]
        det_id = int(d.id_detalle_prestaciones)
        adel = en_adelanto.get(det_id)
        pago_id: Optional[int] = en_liq.get(det_id)
        adelanto_ref: Optional[int] = None
        if adel and adel[0] == adelanto.id:
            estado, adelanto_ref = "en_este", adel[0]
        elif adel and adel[1] == "AP":
            estado, adelanto_ref = "pagada", adel[0]
        elif adel:
            estado, adelanto_ref = "en_otro", adel[0]
        elif d.estado == "L":
            estado = "pagada"
        elif pago_id is not None:
            estado = "en_pago"
        elif r.medico_id is None:
            estado = "sin_medico"
        elif not d.importe_total or d.importe_total <= 0:
            estado = "sin_importe"
        else:
            estado = "disponible"

        n_grupo = integrantes.get(int(d.grupo_equipo_id), 0) if d.grupo_equipo_id else 0
        if not d.grupo_equipo_id:
            equipo = None
        elif int(d.grupo_equipo_id) == det_id:
            equipo = f"Con {max(n_grupo - 1, 0)} integrante{'s' if n_grupo - 1 != 1 else ''} más"
        else:
            equipo = f"{_rotulo_funcion(d.tpo_funcion)} de #{d.grupo_equipo_id}"

        items.append(PrestacionFacturaRow(
            id=det_id,
            fecha_practica=d.fecha_practica,
            codigo=str(d.cod_nom) if d.cod_nom is not None else None,
            descripcion=r.descripcion,
            nro_orden=str(d.nro_orden) if d.nro_orden is not None else None,
            paciente=d.nom_ape_p,
            medico_id=r.medico_id,
            medico_nro_socio=r.nro_socio,
            medico_nombre=(r.medico_nombre or "").strip() or None,
            funcion=_rotulo_funcion(d.tpo_funcion),
            equipo=equipo,
            importe_total=d.importe_total or Decimal("0"),
            estado=estado,
            adelanto_id=adelanto_ref,
            pago_id=pago_id,
        ))

    total_factura = (await db.execute(
        select(func.count()).select_from(D).where(*base)
    )).scalar_one()
    disponibles = (await db.execute(
        select(func.count()).select_from(D)
        .join(ListadoMedico, ListadoMedico.NRO_SOCIO == medico_de_la_fila)
        .where(*base, libre)
    )).scalar_one()

    return PrestacionesFacturaPage(
        items=items, total=int(total), disponibles=int(disponibles), total_factura=int(total_factura)
    )


# ================================================
# POST /{id}/items — agregar prestaciones
# ================================================
@router.post("/{adelanto_id}/items", response_model=AdelantoItemsResultado, status_code=201)
async def agregar_prestaciones(
    adelanto_id: int, payload: AdelantoItemsAdd, db: AsyncSession = Depends(get_db)
):
    """Agrega las prestaciones que se puedan y devuelve las que no, con el motivo."""
    adelanto = await _get_adelanto(db, adelanto_id)
    if adelanto.estado != "A":
        raise HTTPException(409, "Solo se pueden agregar prestaciones a un adelanto abierto")
    factura = await _factura_de(db, adelanto)
    if not factura:
        raise HTTPException(404, "La factura del adelanto ya no existe")

    pedidas = list(dict.fromkeys(payload.detalle_ids))
    filas = {
        int(d.id_detalle_prestaciones): d
        for d in (await db.execute(
            select(D).where(D.id_detalle_prestaciones.in_(pedidas), *_de_la_factura(factura))
        )).scalars().all()
    }
    reservadas = {
        int(det): int(adel) for det, adel in (await db.execute(
            select(LiquidacionAdelantoItem.detalle_facturacion_id, LiquidacionAdelantoItem.adelanto_id)
            .where(LiquidacionAdelantoItem.detalle_facturacion_id.in_(pedidas))
        )).all()
    }
    en_liq = {
        int(x) for x in (await db.execute(
            select(DetalleLiquidacion.cmc_detalle_id).where(DetalleLiquidacion.cmc_detalle_id.in_(pedidas))
        )).scalars().all()
    }
    socios = {
        int(c) for c in {d.cod_med for d in filas.values() if d.cod_med is not None}
    }
    medico_por_socio = {
        int(nro): int(mid) for nro, mid in (await db.execute(
            select(ListadoMedico.NRO_SOCIO, ListadoMedico.ID).where(ListadoMedico.NRO_SOCIO.in_(socios))
        )).all()
    } if socios else {}

    rechazadas: list[dict] = []
    agregadas = 0
    for det_id in pedidas:
        d = filas.get(det_id)
        motivo: Optional[str] = None
        if d is None:
            motivo = "No es de la factura de este adelanto"
        elif det_id in reservadas:
            motivo = (
                "Ya está en este adelanto" if reservadas[det_id] == adelanto.id
                else f"Ya está en el adelanto #{reservadas[det_id]}"
            )
        elif d.estado != "C":
            motivo = "Ya está pagada" if d.estado == "L" else "No está cerrada"
        elif det_id in en_liq:
            motivo = "Ya está en un pago"
        elif d.cod_med is None or int(d.cod_med) not in medico_por_socio:
            motivo = "El médico no está en el padrón"
        elif not d.importe_total or d.importe_total <= 0:
            motivo = "Tiene importe cero"
        if motivo:
            rechazadas.append({"id": det_id, "motivo": motivo})
            continue

        honorarios, gastos = repartir_honorarios_gastos(d)
        db.add(LiquidacionAdelantoItem(
            adelanto_id=adelanto.id,
            detalle_facturacion_id=det_id,
            medico_id=medico_por_socio[int(d.cod_med)],
            honorarios=honorarios,
            gastos=gastos,
            importe_total=to_dec(d.importe_total),
        ))
        agregadas += 1

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Otra persona acaba de adelantar alguna de esas prestaciones. Recargá la lista.")
    return AdelantoItemsResultado(agregadas=agregadas, rechazadas=rechazadas)


# ================================================
# DELETE /{id}/items/{item_id} — quitar una prestación
# ================================================
@router.delete("/{adelanto_id}/items/{item_id}", status_code=204)
async def quitar_prestacion(adelanto_id: int, item_id: int, db: AsyncSession = Depends(get_db)):
    adelanto = await _get_adelanto(db, adelanto_id)
    if adelanto.estado != "A":
        raise HTTPException(409, "Solo se pueden quitar prestaciones de un adelanto abierto")
    item = await db.get(LiquidacionAdelantoItem, item_id)
    if not item or item.adelanto_id != adelanto.id:
        raise HTTPException(404, "La prestación no está en este adelanto")
    await db.delete(item)
    await db.commit()
    return None


# ================================================
# PATCH /{id}/estado — cerrar, reabrir, pasar al pago, quitar del pago
# ================================================
@router.patch("/{adelanto_id}/estado", response_model=AdelantoRead)
async def cambiar_estado(
    adelanto_id: int, payload: AdelantoEstadoPayload, db: AsyncSession = Depends(get_db)
):
    adelanto = await _get_adelanto(db, adelanto_id)
    nuevo = payload.estado
    if adelanto.estado == nuevo:
        raise HTTPException(409, f"El adelanto ya está en estado '{nuevo}'")

    transicion = (adelanto.estado, nuevo)
    pago_afectado: Optional[int] = None

    if transicion == ("A", "C"):
        n = (await db.execute(
            select(func.count(LiquidacionAdelantoItem.id)).where(LiquidacionAdelantoItem.adelanto_id == adelanto.id)
        )).scalar_one()
        if not n:
            raise HTTPException(409, "El adelanto no tiene prestaciones: agregá al menos una antes de cerrarlo")
        adelanto.estado = "C"

    elif transicion == ("C", "A"):
        adelanto.estado = "A"

    elif transicion == ("C", "L"):
        pago = (await db.execute(select(Pago).where(Pago.estado == "A").limit(1))).scalars().first()
        if not pago:
            raise HTTPException(409, "No hay pago abierto al que pasar el adelanto")
        # Por si algo cambió desde que se armó: toda prestación tiene que seguir cerrada y sin pagar.
        ids = [int(i) for i in (await db.execute(
            select(LiquidacionAdelantoItem.detalle_facturacion_id).where(LiquidacionAdelantoItem.adelanto_id == adelanto.id)
        )).scalars().all()]
        ocupadas = (await db.execute(
            select(func.count()).select_from(D).where(
                D.id_detalle_prestaciones.in_(ids),
                or_(D.estado != "C", _en_alguna_liquidacion()),
            )
        )).scalar_one()
        if ocupadas:
            raise HTTPException(
                409, f"{ocupadas} prestación(es) del adelanto ya se pagaron o están en un pago. Reabrí el adelanto y revisalo."
            )
        adelanto.pago_id = pago.id
        adelanto.estado = "L"
        pago_afectado = pago.id

    elif transicion == ("L", "C"):
        pago = await db.get(Pago, adelanto.pago_id) if adelanto.pago_id else None
        if pago and pago.estado != "A":
            raise HTTPException(409, "No se puede quitar un adelanto de un pago cerrado")
        pago_afectado = adelanto.pago_id
        adelanto.pago_id = None
        adelanto.estado = "C"

    else:
        if adelanto.estado == "AP":
            raise HTTPException(
                409,
                "El adelanto está pagado y es inmutable. Solo vuelve a 'C' si el pago es eliminado.",
            )
        raise HTTPException(
            409, f"Transición '{adelanto.estado}' → '{nuevo}' no permitida. Válidas: A→C, C→A, C→L, L→C"
        )

    if pago_afectado:
        # El adelanto cuenta como producción del médico: cambian las bases de los porcentuales
        # y quién participa del pago.
        await generar_y_recalcular_porcentuales(db, pago_afectado)

    await db.commit()
    await db.refresh(adelanto)
    return await _leer(db, adelanto)
