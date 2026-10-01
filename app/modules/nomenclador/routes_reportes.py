import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models.catalogs import ObrasSociales
from app.db.models.nomenclador_cmc import HistorialPrecioCodigo, NomencladorCMC, Valor
from app.modules.nomenclador import service, service_vias
from app.modules.nomenclador.service import prioridad_origen
from app.modules.nomenclador.schemas import (
    Origen,
    BoletinItemOut,
    BoletinOut,
    ComponenteLookupOut,
    EvolucionPrecioItem,
    RankingItem,
    RankingValoresOut,
    TablaValoresItem,
)

router = APIRouter()


def _componentes_from_snapshot(snapshot: list) -> list:
    from app.modules.nomenclador.schemas import BoletinComponenteOut
    out = []
    for item in snapshot:
        out.append(BoletinComponenteOut(
            concepto=item["concepto"],
            tipo=item["tipo"],
            valor_unitario=Decimal(item["valor_unitario"]) if item.get("valor_unitario") else None,
            cantidad=Decimal(item["cantidad"]) if item.get("cantidad") else None,
            subtotal=Decimal(item["subtotal"]),
        ))
    return out


async def _nombre_os(obra_social_nro: int, db: AsyncSession) -> str:
    stmt = select(ObrasSociales).where(ObrasSociales.NRO_OBRASOCIAL == obra_social_nro)
    os = (await db.execute(stmt)).scalar_one_or_none()
    return os.OBRA_SOCIAL if os else str(obra_social_nro)


async def _nombres_os(nros: list[int], db: AsyncSession) -> dict[int, str]:
    """Los nombres de varias obras sociales, en una sola consulta."""
    if not nros:
        return {}
    filas = (
        await db.execute(
            select(ObrasSociales.NRO_OBRASOCIAL, ObrasSociales.OBRA_SOCIAL).where(
                ObrasSociales.NRO_OBRASOCIAL.in_(nros)
            )
        )
    ).all()
    return {nro: nombre for nro, nombre in filas}


@router.get("/ranking_valores", response_model=RankingValoresOut)
async def ranking_valores(
    fecha_referencia: Optional[datetime.date] = Query(None),
    codigo: str = Query("420101", description="Código CMC de consulta a comparar"),
    db: AsyncSession = Depends(get_db),
):
    """Lista todas las OS con su valor del código dado, ordenadas de mayor a menor."""
    fecha = fecha_referencia or datetime.date.today()

    # Buscar el nomenclador por código
    stmt_nom = select(NomencladorCMC).where(
        NomencladorCMC.codigo == codigo, NomencladorCMC.activo == True
    )
    nom = (await db.execute(stmt_nom)).scalar_one_or_none()
    if not nom:
        raise HTTPException(404, f"Código '{codigo}' no encontrado en el nomenclador")

    stmt = select(HistorialPrecioCodigo).where(
        HistorialPrecioCodigo.nomenclador_id == nom.id,
        HistorialPrecioCodigo.vigencia_desde <= fecha,
        (HistorialPrecioCodigo.vigencia_hasta.is_(None))
        | (HistorialPrecioCodigo.vigencia_hasta >= fecha),
    )
    historiales = (await db.execute(stmt)).scalars().all()

    # Una fila por obra social, no una por vigencia. El filtro es por rango y
    # muchas filas viejas quedaron con `vigencia_hasta` en NULL, así que una
    # misma obra social puede traer decenas de vigencias que cubren la fecha
    # (el 420351 devuelve 2.121 filas para 53 obras sociales). Sin esto el
    # ranking la lista varias veces, en posiciones distintas y con precios
    # distintos. Vale la vigencia más reciente; a igual fecha, el precio mayor.
    ultima: dict[int, HistorialPrecioCodigo] = {}
    for h in historiales:
        previa = ultima.get(h.obra_social_nro)
        if (
            previa is None
            or h.vigencia_desde > previa.vigencia_desde
            or (
                h.vigencia_desde == previa.vigencia_desde
                and h.precio_total > previa.precio_total
            )
        ):
            ultima[h.obra_social_nro] = h

    # Los nombres en una sola lectura: antes iba un SELECT por fila.
    nombres = await _nombres_os(list(ultima), db)

    ordenados = sorted(ultima.values(), key=lambda h: h.precio_total, reverse=True)
    ranking = [
        RankingItem(
            posicion=pos,
            obra_social_nro=h.obra_social_nro,
            nombre_os=nombres.get(h.obra_social_nro, str(h.obra_social_nro)),
            valor=h.precio_total,
        )
        for pos, h in enumerate(ordenados, start=1)
    ]

    return RankingValoresOut(
        fecha_referencia=fecha,
        codigo_consulta=codigo,
        ranking=ranking,
    )


@router.get("/boletin", response_model=BoletinOut)
async def boletin(
    fecha: datetime.date = Query(...),
    obra_social_nro: Optional[int] = Query(None),
    codigo: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Consulta detallada de valores por OS y/o código a una fecha."""
    stmt = select(HistorialPrecioCodigo).where(
        HistorialPrecioCodigo.vigencia_desde <= fecha,
        (HistorialPrecioCodigo.vigencia_hasta.is_(None))
        | (HistorialPrecioCodigo.vigencia_hasta >= fecha),
    )
    if obra_social_nro:
        stmt = stmt.where(HistorialPrecioCodigo.obra_social_nro == obra_social_nro)

    if codigo:
        nom = await service.resolver_nomenclador(db, codigo)
        if not nom:
            raise HTTPException(404, f"Código '{codigo}' no encontrado")
        stmt = stmt.where(HistorialPrecioCodigo.nomenclador_id == nom.id)

    stmt = stmt.order_by(HistorialPrecioCodigo.obra_social_nro, HistorialPrecioCodigo.nomenclador_id)
    result = await db.execute(stmt)
    historiales = result.scalars().all()

    # Los nomencladores y valores se leen en lote y no uno por fila: sin OS en
    # contexto el boletín de un código devuelve una fila por cada vigencia que
    # cubre la fecha (hoy el 420351 pasa las dos mil), y un db.get por fila eran
    # más de cuatro mil idas a la base para armar una sola respuesta.
    nom_ids = {h.nomenclador_id for h in historiales if h.nomenclador_id}
    val_ids = {h.valores_id for h in historiales if h.valores_id}

    noms = {}
    if nom_ids:
        filas = await db.execute(
            select(NomencladorCMC).where(NomencladorCMC.id.in_(nom_ids))
        )
        noms = {n.id: n for n in filas.scalars()}

    valores = {}
    if val_ids:
        filas = await db.execute(select(Valor).where(Valor.id.in_(val_ids)))
        valores = {v.id: v for v in filas.scalars()}

    items = []
    for h in historiales:
        nom = await db.get(NomencladorCMC, h.nomenclador_id)
        valor = await db.get(Valor, h.valores_id)
        legacy = None
        if nom is not None and not (valor and valor.descripcion and valor.descripcion.strip()):
            mapa = await service.descripciones_legacy(db, {(nom.codigo, h.obra_social_nro)})
            legacy = mapa.get((nom.codigo, h.obra_social_nro))
        items.append(BoletinItemOut(
            obra_social_nro=h.obra_social_nro,
            codigo=nom.codigo if nom else str(h.nomenclador_id),
            origen=h.origen,
            descripcion=service.descripcion_efectiva(valor, legacy),
            nivel=valor.nivel if valor else None,
            por_presupuesto=bool(valor and valor.por_presupuesto),
            precio_total=h.precio_total,
            componentes=_componentes_from_snapshot(h.componentes_snapshot),
            vigencia_desde=h.vigencia_desde,
            vigencia_hasta=h.vigencia_hasta,
        ))

    return BoletinOut(fecha=fecha, obra_social_nro=obra_social_nro, items=items)


@router.get("/tabla_valores", response_model=List[TablaValoresItem])
async def tabla_valores(
    obra_social_nro: int = Query(...),
    fecha: Optional[datetime.date] = Query(None),
    codigo: Optional[str] = Query(None),
    especialidades: Optional[List[int]] = Query(
        None,
        description=(
            "IDs de especialidad del colegio (ID_COLEGIO_ESPE) del médico, en orden de "
            "prioridad (principal primero). Si se envía, entre las variantes NE de cada "
            "código gana la que matchee la especialidad del médico de mayor rango."
        ),
    ),
    orden: str = Query("codigo", pattern="^(codigo|valor)$"),
    via: str = Query("T", description="T = tradicional, L = laparoscópica"),
    page: int = Query(1, ge=1),
    size: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Códigos vigentes de una OS con su precio a una fecha, UNA fila por código.

    Pensado para consultar por `codigo` (el médico/operador ingresa 420101 y obtiene esa
    única fila con la variante que le corresponde). Sin `codigo` lista toda la OS, igual
    colapsada a una fila por código.

    Cada código puede tener varias variantes. Se devuelve solo la de mayor "peso" según la
    misma prioridad que el lookup de facturación: origen (NE>NN) → match de
    especialidad por orden de slots → vigencia más reciente.
    - Con `especialidades`: NE entra en juego (gana la que matchee la especialidad de
      mayor rango del médico); si ninguna NE aplica, cae a NN.
    - Sin `especialidades`: NE queda fuera; compite solo NN.
    Si `codigo` no existe, se devuelve vacío.

    `via=L`: los códigos elegibles (Honorarios con galeno de cirugía adulto/infantil)
    devuelven el precio laparoscópico. Los que no admiten laparoscopía NO rompen el
    listado: la fila cae a su precio tradicional con `via_aplicada="T"` (es un listado,
    no una cotización puntual — el rechazo estricto queda para el lookup de facturación).
    """
    fecha_ref = fecha or datetime.date.today()

    async def _build_item(h: HistorialPrecioCodigo) -> TablaValoresItem:
        nom = await db.get(NomencladorCMC, h.nomenclador_id)
        valor = await db.get(Valor, h.valores_id)

        componentes = [
            ComponenteLookupOut(
                componente_id=item.get("componente_id"),
                concepto=item["concepto"],
                tipo=item["tipo"],
                galeno_id=item.get("galeno_id"),
                galeno_codigo=item.get("galeno_codigo"),
                galeno_nivel=item.get("galeno_nivel"),
                cantidad=Decimal(item["cantidad"]),
                valor_unitario=Decimal(item["valor_unitario"]),
                subtotal=Decimal(item["subtotal"]),
            )
            for item in h.componentes_snapshot
        ]
        precio_total = h.precio_total
        via_aplicada = service_vias.VIA_TRADICIONAL
        try:
            componentes, precio_total, _nivel = await service_vias.ajustar_componentes_por_via(
                db, h.obra_social_nro, componentes, via,
            )
            via_aplicada = via
        except service_vias.ViaNoAplicableError:
            pass  # código no elegible para laparoscopía → queda con el precio tradicional

        legacy = None
        if nom is not None and not (valor and valor.descripcion and valor.descripcion.strip()):
            mapa = await service.descripciones_legacy(db, {(nom.codigo, h.obra_social_nro)})
            legacy = mapa.get((nom.codigo, h.obra_social_nro))

        return TablaValoresItem(
            nomenclador_id=h.nomenclador_id,
            codigo=nom.codigo if nom else str(h.nomenclador_id),
            origen=h.origen,
            especialidad_id_colegio=h.especialidad_id_colegio,
            descripcion=service.descripcion_efectiva(valor, legacy),
            nivel=valor.nivel if valor else None,
            por_presupuesto=bool(valor and valor.por_presupuesto),
            sin_restriccion_especialidad=bool(valor and valor.sin_restriccion_especialidad),
            precio_total=precio_total,
            vigencia_desde=h.vigencia_desde,
            vigencia_hasta=h.vigencia_hasta,
            componentes=[c.model_dump(mode="json") for c in componentes],
            via_aplicada=via_aplicada,
        )

    base_filters = [
        HistorialPrecioCodigo.obra_social_nro == obra_social_nro,
        HistorialPrecioCodigo.vigencia_desde <= fecha_ref,
        (HistorialPrecioCodigo.vigencia_hasta.is_(None))
        | (HistorialPrecioCodigo.vigencia_hasta >= fecha_ref),
    ]
    if codigo:
        # La tabla es siempre de una OS: el código resuelve a su fila propia si la
        # tiene, y si no a la compartida.
        nom = await service.resolver_nomenclador(db, codigo, obra_social_nro)
        if not nom:
            return []  # código inexistente → sin resultados
        base_filters.append(HistorialPrecioCodigo.nomenclador_id == nom.id)

    # Traemos todas las variantes vigentes (más reciente primero para desempatar) y
    # colapsamos en Python: la elección por peso depende del perfil del médico.
    stmt = (
        select(HistorialPrecioCodigo)
        .where(*base_filters)
        .order_by(HistorialPrecioCodigo.vigencia_desde.desc())
    )
    filas = (await db.execute(stmt)).scalars().all()

    # Perfil del médico: orden de slots (principal = índice 0 = mejor rank).
    especialidades = especialidades or []
    slot_rank = {esp: i for i, esp in enumerate(especialidades)}
    _SLOT_SIN_ESP = len(especialidades) + 1

    # Códigos sin restricción de especialidad en esta OS: su NE sin especialidad es el
    # precio para cualquier médico (ver service.lookup_precio).
    nom_ids_ne_sin_esp = {
        f.nomenclador_id for f in filas
        if f.origen == Origen.NE.value and f.especialidad_id_colegio is None
    }
    sin_restriccion_ids: set[int] = set()
    if nom_ids_ne_sin_esp:
        sin_restriccion_ids = set((await db.execute(
            select(Valor.nomenclador_id).where(
                Valor.obra_social_nro == obra_social_nro,
                Valor.nomenclador_id.in_(nom_ids_ne_sin_esp),
                Valor.estado == "activo",
                Valor.sin_restriccion_especialidad == True,
            ).distinct()
        )).scalars().all())

    def _aplicable(fila: HistorialPrecioCodigo) -> bool:
        # NN siempre entra en juego. NE con especialidad solo aplica si se pasó el
        # perfil del médico y este la posee; NE sin especialidad, si el código es sin
        # restricción en esta OS (aplica a cualquier perfil, incluso sin especialidades).
        if fila.origen == Origen.NE.value:
            if fila.especialidad_id_colegio is None:
                return fila.nomenclador_id in sin_restriccion_ids
            return bool(especialidades) and fila.especialidad_id_colegio in slot_rank
        return True

    def _orden_variante(fila: HistorialPrecioCodigo):
        # Menor gana: prioridad de origen (NE>NN) → match de especialidad por orden
        # de slots → vigencia más reciente como desempate final.
        rank = (
            slot_rank.get(fila.especialidad_id_colegio, _SLOT_SIN_ESP)
            if fila.especialidad_id_colegio is not None
            else _SLOT_SIN_ESP
        )
        return (prioridad_origen(fila.origen), rank, -fila.vigencia_desde.toordinal())

    # Una sola fila elegida por código (nomenclador_id).
    elegidas: dict[int, HistorialPrecioCodigo] = {}
    for fila in filas:
        if not _aplicable(fila):
            continue
        actual = elegidas.get(fila.nomenclador_id)
        if actual is None or _orden_variante(fila) < _orden_variante(actual):
            elegidas[fila.nomenclador_id] = fila

    seleccion = list(elegidas.values())

    # Orden final + paginación a nivel de código (ya colapsado).
    if orden == "valor":
        seleccion.sort(key=lambda h: h.precio_total, reverse=True)
    else:
        codigos: dict[int, str] = {}
        for h in seleccion:
            nom = await db.get(NomencladorCMC, h.nomenclador_id)
            codigos[h.nomenclador_id] = nom.codigo if nom else str(h.nomenclador_id)
        seleccion.sort(key=lambda h: codigos[h.nomenclador_id])

    inicio = (page - 1) * size
    pagina = seleccion[inicio : inicio + size]
    return [await _build_item(h) for h in pagina]


@router.get("/evolucion_precios", response_model=List[EvolucionPrecioItem])
async def evolucion_precios(
    nomenclador_id: int = Query(...),
    obra_social_nro: int = Query(...),
    desde: Optional[datetime.date] = Query(None),
    hasta: Optional[datetime.date] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Serie temporal del precio de un código para una OS."""
    stmt = select(HistorialPrecioCodigo).where(
        HistorialPrecioCodigo.nomenclador_id == nomenclador_id,
        HistorialPrecioCodigo.obra_social_nro == obra_social_nro,
    )
    if desde:
        stmt = stmt.where(HistorialPrecioCodigo.vigencia_desde >= desde)
    if hasta:
        stmt = stmt.where(HistorialPrecioCodigo.vigencia_desde <= hasta)
    stmt = stmt.order_by(HistorialPrecioCodigo.vigencia_desde)
    result = await db.execute(stmt)
    historiales = result.scalars().all()

    return [
        EvolucionPrecioItem(
            vigencia_desde=h.vigencia_desde,
            vigencia_hasta=h.vigencia_hasta,
            precio_total=h.precio_total,
            motivo_cambio=h.motivo_cambio,
            fecha_cambio=h.fecha_cambio,
        )
        for h in historiales
    ]
