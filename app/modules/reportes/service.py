"""Agregaciones de Reportes y Estadísticas.

Reglas que valen para TODO el módulo:

* **Se agrega en SQL, nunca en Python.** Un período entero de
  `detalle_facturacion` son decenas de miles de filas: traerlas para sumarlas
  acá reventaría la memoria y la red. Todo sale con `GROUP BY` + `LIMIT`.
* **Sólo cuenta lo que factura**: `estado='A'`. Las anuladas ('X'), las
  rechazadas y las pendientes de validación quedan afuera de los importes —
  igual que en liquidación y en las facturas.
* **Los filtros pegan a los índices existentes**: `idx_po (cod_obr, periodo)` e
  `idx_match_d (cod_med, periodo, cod_obr, cod_nom, …)`. Por eso `periodo` es
  obligatorio en casi todos los endpoints: sin él, cualquier consulta es un full
  scan de la tabla.
* **El módulo es de sólo lectura.** No escribe nada, en ninguna tabla.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy import Select, and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DetalleFacturacionCMC as D
from app.db.models import ListadoMedico
from app.db.models.catalogs import ObrasSociales
from app.db.models.nomenclador_cmc import NomencladorCMC

CERO = Decimal("0.00")

# Sólo las filas activas suman. Ver docstring del módulo.
DETALLE_ACTIVO = "A"

# Tope duro de filas por consulta. Ningún parámetro del cliente puede superarlo:
# es lo que impide que alguien pida "todo" y tire abajo la base.
MAX_LIMIT = 200


def _filtros(
    *,
    periodo: Optional[str] = None,
    obra_social: Optional[str] = None,
    nro_socio: Optional[str] = None,
    codigo: Optional[str] = None,
    desde=None,
    hasta=None,
    validacion_estado: Optional[str] = None,
) -> list:
    """Condiciones comunes. El orden importa poco: lo resuelve el optimizador."""
    cond = [D.estado == DETALLE_ACTIVO]
    if periodo:
        cond.append(D.periodo == periodo)
    if obra_social:
        cond.append(D.cod_obr == str(obra_social))
    if nro_socio:
        cond.append(D.cod_med == str(nro_socio))
    if codigo:
        cond.append(D.cod_nom == codigo)
    if desde:
        cond.append(D.fecha_practica >= desde)
    if hasta:
        cond.append(D.fecha_practica <= hasta)
    if validacion_estado:
        cond.append(D.validacion_estado == validacion_estado)
    return cond


def _limitar(limit: int) -> int:
    return max(1, min(int(limit or 50), MAX_LIMIT))


def _txt(v) -> str:
    """Identificador siempre como texto.

    OJO: el modelo declara `cod_med` y `cod_obr` como String, pero en la base
    son `bigint(10)` y `smallint(4)`. El driver devuelve int, y los schemas de
    salida esperan str — sin esta coerción el endpoint responde 500 con un
    ResponseValidationError. Se arregla acá y no en el modelo porque esas
    columnas las comparten facturación y liquidación, y cambiarles el tipo
    tendría alcance mucho mayor que este módulo.
    """
    return "" if v is None else str(v)


def _like(q: str) -> str:
    """Patrón LIKE con los comodines del usuario escapados.

    Sin esto, un `%` escrito en el buscador fuerza un full scan y un `_` matchea
    cualquier carácter sin que quien busca lo haya pedido.
    """
    limpio = q.strip().replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{limpio}%"


async def _codigos_que_matchean(db: AsyncSession, q: str) -> list[str]:
    """Códigos cuyo NÚMERO o DESCRIPCIÓN contienen el texto buscado.

    La descripción vive en `nm_nomenclador`, no en `detalle_facturacion`, así
    que se resuelve primero acá y después se filtra la agregación por `IN`. Es
    más barato que joinear el nomenclador dentro del GROUP BY, y además permite
    buscar por texto ("consulta") además de por código.
    """
    patron = _like(q)
    filas = (
        await db.execute(
            select(NomencladorCMC.codigo).where(
                NomencladorCMC.codigo.like(patron, escape="!")
                | NomencladorCMC.descripcion.like(patron, escape="!")
            )
            # Tope: si alguien busca una letra sola no tiene sentido armar un IN
            # con miles de códigos — con 500 alcanza para acotar de sobra.
            .limit(500)
        )
    ).scalars().all()
    return list(filas)


async def _socios_que_matchean(db: AsyncSession, q: str) -> list[str]:
    """NRO_SOCIO cuyo número o nombre contienen el texto buscado."""
    patron = _like(q)
    cond = ListadoMedico.NOMBRE.like(patron, escape="!")
    if q.strip().isdigit():
        cond = cond | (ListadoMedico.NRO_SOCIO == int(q.strip()))
    filas = (
        await db.execute(select(ListadoMedico.NRO_SOCIO).where(cond).limit(500))
    ).scalars().all()
    return [str(f) for f in filas]


async def _nombres_medicos(db: AsyncSession, socios: Sequence[str]) -> dict[str, str]:
    """NRO_SOCIO → NOMBRE, en UNA consulta.

    Se resuelve aparte y no con un JOIN para no arrastrar `listado_medico` a
    través del GROUP BY (la agregación queda más chica y el plan más simple).
    """
    if not socios:
        return {}
    filas = (
        await db.execute(
            select(ListadoMedico.NRO_SOCIO, ListadoMedico.NOMBRE).where(
                ListadoMedico.NRO_SOCIO.in_([int(s) for s in socios if str(s).isdigit()])
            )
        )
    ).all()
    return {str(nro): nombre for nro, nombre in filas}


async def _nombres_obras(db: AsyncSession, nros: Sequence[str]) -> dict[str, str]:
    if not nros:
        return {}
    filas = (
        await db.execute(
            select(ObrasSociales.NRO_OBRASOCIAL, ObrasSociales.OBRA_SOCIAL).where(
                ObrasSociales.NRO_OBRASOCIAL.in_(
                    [int(n) for n in nros if str(n).isdigit()]
                )
            )
        )
    ).all()
    return {str(nro): nombre for nro, nombre in filas}


async def _descripciones(db: AsyncSession, codigos: Sequence[str]) -> dict[str, str]:
    if not codigos:
        return {}
    filas = (
        await db.execute(
            select(NomencladorCMC.codigo, NomencladorCMC.descripcion).where(
                NomencladorCMC.codigo.in_(list(codigos))
            )
        )
    ).all()
    return {c: d for c, d in filas}


# ── Resumen ───────────────────────────────────────────────────────────────────

async def resumen(
    db: AsyncSession, *, periodo: str, nro_socio: Optional[str] = None
) -> dict:
    """KPIs de un período. Una sola consulta con todos los agregados."""
    cond = _filtros(periodo=periodo, nro_socio=nro_socio)
    fila = (
        await db.execute(
            select(
                func.count(D.id_detalle_prestaciones),
                func.coalesce(func.sum(D.importe_total), CERO),
                func.coalesce(func.sum(D.honorarios), CERO),
                func.coalesce(func.sum(D.gastos), CERO),
                func.count(func.distinct(D.cod_med)),
                func.count(func.distinct(D.cod_obr)),
                func.count(func.distinct(D.cod_nom)),
            ).where(and_(*cond))
        )
    ).one()

    return {
        "periodo": periodo,
        "prestaciones": int(fila[0] or 0),
        "importe_total": fila[1] or CERO,
        "honorarios": fila[2] or CERO,
        "gastos": fila[3] or CERO,
        "medicos": int(fila[4] or 0),
        "obras_sociales": int(fila[5] or 0),
        "codigos": int(fila[6] or 0),
    }


# ── Ranking por código ────────────────────────────────────────────────────────

ORDENES_CODIGO = {
    "importe": desc(func.coalesce(func.sum(D.importe_total), CERO)),
    "cantidad": desc(func.coalesce(func.sum(D.cantidad), 0)),
    "prestaciones": desc(func.count(D.id_detalle_prestaciones)),
    "codigo": D.cod_nom.asc(),
}


async def por_codigo(
    db: AsyncSession,
    *,
    periodo: str,
    obra_social: Optional[str] = None,
    nro_socio: Optional[str] = None,
    especialidad_id: Optional[int] = None,
    q: Optional[str] = None,
    orden: str = "importe",
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Qué se facturó de cada código. Es la "tabla de códigos" con filtros.

    `especialidad_id` filtra por la especialidad con la que se cargó la
    prestación (`id_especialidad`), no por la del médico: es el dato que quedó
    asentado en la fila.
    """
    cond = _filtros(periodo=periodo, obra_social=obra_social, nro_socio=nro_socio)
    if especialidad_id is not None:
        cond.append(D.id_especialidad == especialidad_id)

    if q and q.strip():
        codigos = await _codigos_que_matchean(db, q)
        if not codigos:
            return []  # nada matchea: no hace falta ir a la tabla grande
        cond.append(D.cod_nom.in_(codigos))

    stmt: Select = (
        select(
            D.cod_nom,
            func.count(D.id_detalle_prestaciones),
            func.coalesce(func.sum(D.cantidad), 0),
            func.coalesce(func.sum(D.importe_total), CERO),
            func.count(func.distinct(D.cod_med)),
        )
        .where(and_(*cond), D.cod_nom.isnot(None))
        .group_by(D.cod_nom)
        .order_by(ORDENES_CODIGO.get(orden, ORDENES_CODIGO["importe"]))
        .limit(_limitar(limit))
        .offset(max(0, int(offset or 0)))
    )

    filas = (await db.execute(stmt)).all()
    desc_map = await _descripciones(db, [f[0] for f in filas])

    return [
        {
            "codigo": _txt(f[0]),
            "descripcion": desc_map.get(f[0]),
            "prestaciones": int(f[1] or 0),
            "cantidad": int(f[2] or 0),
            "importe_total": f[3] or CERO,
            "medicos": int(f[4] or 0),
        }
        for f in filas
    ]


# ── Ranking por médico ────────────────────────────────────────────────────────

async def por_medico(
    db: AsyncSession,
    *,
    periodo: str,
    codigo: Optional[str] = None,
    obra_social: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Quiénes facturaron más. Con `codigo` responde "quiénes facturaron este
    código"; con `obra_social`, "quiénes facturaron más a esta obra social"."""
    cond = _filtros(periodo=periodo, codigo=codigo, obra_social=obra_social)

    if q and q.strip():
        socios = await _socios_que_matchean(db, q)
        if not socios:
            return []
        cond.append(D.cod_med.in_(socios))

    filas = (
        await db.execute(
            select(
                D.cod_med,
                func.count(D.id_detalle_prestaciones),
                func.coalesce(func.sum(D.cantidad), 0),
                func.coalesce(func.sum(D.importe_total), CERO),
            )
            .where(and_(*cond))
            .group_by(D.cod_med)
            .order_by(desc(func.coalesce(func.sum(D.importe_total), CERO)))
            .limit(_limitar(limit))
            .offset(max(0, int(offset or 0)))
        )
    ).all()

    nombres = await _nombres_medicos(db, [f[0] for f in filas])

    return [
        {
            "nro_socio": _txt(f[0]),
            "nombre": nombres.get(_txt(f[0])),
            "prestaciones": int(f[1] or 0),
            "cantidad": int(f[2] or 0),
            "importe_total": f[3] or CERO,
        }
        for f in filas
    ]


# ── Ranking por obra social ───────────────────────────────────────────────────

async def por_obra_social(
    db: AsyncSession,
    *,
    periodo: str,
    nro_socio: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    cond = _filtros(periodo=periodo, nro_socio=nro_socio)

    filas = (
        await db.execute(
            select(
                D.cod_obr,
                func.count(D.id_detalle_prestaciones),
                func.coalesce(func.sum(D.importe_total), CERO),
                func.count(func.distinct(D.cod_med)),
            )
            .where(and_(*cond), D.cod_obr.isnot(None))
            .group_by(D.cod_obr)
            .order_by(desc(func.coalesce(func.sum(D.importe_total), CERO)))
            .limit(_limitar(limit))
        )
    ).all()

    nombres = await _nombres_obras(db, [f[0] for f in filas])

    return [
        {
            "obra_social_nro": _txt(f[0]),
            "nombre": nombres.get(_txt(f[0])),
            "prestaciones": int(f[1] or 0),
            "importe_total": f[2] or CERO,
            "medicos": int(f[3] or 0),
        }
        for f in filas
    ]


# ── Listado detallado (el único que devuelve filas, no agregados) ─────────────

async def prestaciones(
    db: AsyncSession,
    *,
    obra_social: Optional[str] = None,
    periodo: Optional[str] = None,
    nro_socio: Optional[str] = None,
    codigo: Optional[str] = None,
    validacion_estado: Optional[str] = None,
    desde=None,
    hasta=None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Prestación por prestación, paginado, con el total aparte para el paginador."""
    cond = _filtros(
        periodo=periodo,
        obra_social=obra_social,
        nro_socio=nro_socio,
        codigo=codigo,
        desde=desde,
        hasta=hasta,
        validacion_estado=validacion_estado,
    )

    total = int(
        (
            await db.execute(
                select(func.count(D.id_detalle_prestaciones)).where(and_(*cond))
            )
        ).scalar_one()
        or 0
    )

    filas = (
        await db.execute(
            select(
                D.id_detalle_prestaciones,
                D.fecha_practica,
                D.periodo,
                D.cod_nom,
                D.cod_med,
                D.nom_ape_p,
                D.dni_p,
                D.cantidad,
                D.importe_total,
                D.autorizacion,
                D.validacion_estado,
            )
            .where(and_(*cond))
            .order_by(desc(D.fecha_practica), desc(D.id_detalle_prestaciones))
            .limit(_limitar(limit))
            .offset(max(0, int(offset or 0)))
        )
    ).all()

    nombres = await _nombres_medicos(db, [f[4] for f in filas])
    desc_map = await _descripciones(db, [f[3] for f in filas if f[3]])

    items = [
        {
            "id": f[0],
            "fecha": f[1],
            "periodo": _txt(f[2]),
            "codigo": _txt(f[3]) or None,
            "descripcion": desc_map.get(f[3]) if f[3] else None,
            "nro_socio": _txt(f[4]),
            "medico": nombres.get(_txt(f[4])),
            "afiliado": f[5],
            "nro_afiliado": f[6],
            "cantidad": int(f[7] or 0),
            "importe_total": f[8] or CERO,
            "autorizacion": f[9],
            "validacion_estado": f[10],
        }
        for f in filas
    ]
    return {"items": items, "total": total}


# ── Serie temporal ────────────────────────────────────────────────────────────

async def evolucion(
    db: AsyncSession,
    *,
    nro_socio: Optional[str] = None,
    obra_social: Optional[str] = None,
    meses: int = 12,
) -> list[dict]:
    """Últimos N períodos con actividad, del más viejo al más nuevo.

    Se ordena descendente en SQL para quedarse con los N más recientes y recién
    después se da vuelta: al revés habría que leer todos los períodos.
    """
    cond = _filtros(nro_socio=nro_socio, obra_social=obra_social)
    tope = max(1, min(int(meses or 12), 36))

    filas = (
        await db.execute(
            select(
                D.periodo,
                func.count(D.id_detalle_prestaciones),
                func.coalesce(func.sum(D.importe_total), CERO),
            )
            .where(and_(*cond))
            .group_by(D.periodo)
            .order_by(desc(D.periodo))
            .limit(tope)
        )
    ).all()

    return [
        {
            "periodo": _txt(f[0]),
            "prestaciones": int(f[1] or 0),
            "importe_total": f[2] or CERO,
        }
        for f in reversed(filas)
    ]
