"""Recotizar prestaciones ya cargadas con el precio vigente.

Un solo cálculo por fila (`recotizar_fila`) que usan dos acciones:

* **Recálculo de factura** (`recalcular_precios`): todas las prestaciones abiertas
  de una O.S. + período, o sólo las de un código. Con vista previa (`dry_run`).
* **Revalorizar** (`revalorizar.py`): después de cargar un precio, las que habían
  quedado en $0 (`sin_valorizar`) de ese código.

Reglas de la fila (decididas con el usuario, 2026-10-05):

* Sólo cálculo automático (`manual='A'`). Las manuales nunca se tocan, aunque
  vengan de validaciones: ésas también se recotizan si son automáticas.
* Fecha: la de la práctica; sin fecha, hoy — igual que la carga
  (`service.fecha_para_precio`). La ventana de 6 meses no corre acá: frena la
  carga de prácticas viejas, no la recotización de lo ya cargado.
* Especialidad: la decide `lookup_precio` (NE > NN, después el orden de los
  casilleros del médico: gana el primero).
* Conceptos: se cotiza lo que la fila ya cobraba (> 0), o lo que dice la marca
  `sin_valorizar` si quedó en $0. Así una fila de ayudante sigue siendo de
  ayudante y una de sólo honorarios no gana gastos.
* Total: se guarda con la misma fórmula que ya tenía la fila — cantidad,
  sesión, porcentaje y coseguro como en la carga, o el coseguro descontado una
  sola vez si vino de validaciones (ver `formula_de`).
* Sin precio, por presupuesto, no admitida o con un total que no sale de sus
  montos: la fila queda como está y se informa el motivo.

Sólo se escriben montos (honorarios, gastos, ayudante, coseguro, importe_total),
`tpo_funcion`, `calculo_snapshot` y `sin_valorizar`. Prestador, clínica, tipo,
período, versión, estado y `publicado` no se tocan.
"""
from __future__ import annotations

import bisect
import contextlib
import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.money import quantize_money
from app.db.models.cmc_facturacion import DetalleFacturacionCMC
from app.db.models.nomenclador_cmc import HistorialPrecioCodigo, MedicoCodigoHabilitado
from app.modules.facturacion import service
from app.modules.nomenclador import service as service_nm, service_vias

CERO = Decimal("0")

MOTIVO_MANUAL = "Cálculo manual: los montos los fijó el operador"
MOTIVO_PRESUPUESTO = "Código por presupuesto: el monto lo carga el operador"
MOTIVO_PRECIO_CERO = "El precio vigente está cargado en $0: se dejó el importe que tenía"
MOTIVO_TOTAL_RARO = (
    "El total guardado no sale de sus montos, cantidad, sesión y coseguro: "
    "se dejó como está para no cambiarle la forma de cálculo"
)


@dataclass
class Recotizacion:
    """Lo que resultaría de recotizar una fila. No toca la fila (ver `aplicar`)."""

    estado: Literal["cambia", "igual", "omitida"]
    fecha: Optional[datetime.date] = None
    motivo: Optional[str] = None
    # Omitida porque el código sigue sin precio vigente (Revalorizar lo distingue).
    sin_precio: bool = False
    honorarios: Decimal = CERO
    gastos: Decimal = CERO
    ayudante: Decimal = CERO
    coseguro: Decimal = CERO
    importe_total: Decimal = CERO
    tpo_funcion: Optional[str] = None
    snapshot: Optional[list] = field(default=None, repr=False)


def _conceptos(row: DetalleFacturacionCMC) -> tuple[bool, bool, bool]:
    """Qué conceptos cobra la fila: los de la marca si quedó sin valorizar, si no
    los que están en > 0.

    Todo en 0 y sin marca (cargadas en $0 antes de que existiera la marca): se
    deduce del `tpo_funcion` guardado. Ayudante ('A') cobra sólo la columna de
    ayudante, esté o no en un equipo; sólo gastos ('G'), gastos; el resto (médico,
    pediatra) honorarios + gastos del precio — nunca el ayudante, que no se suma en
    la fila del médico."""
    if row.sin_valorizar:
        return tuple(c in row.sin_valorizar for c in "HGA")  # type: ignore[return-value]
    montos = (
        service._dec(row.honorarios or 0) > 0,
        service._dec(row.gastos or 0) > 0,
        service._dec(row.ayudante or 0) > 0,
    )
    if any(montos):
        return montos
    tpo = (row.tpo_funcion or "").upper()
    if tpo == "A":
        return False, False, True
    if tpo == "G":
        return False, True, False
    return True, True, False


def conceptos_de(row: DetalleFacturacionCMC) -> str:
    """`_conceptos` como texto ("HG", "A"…), para mostrar."""
    return "".join(c for c, si in zip("HGA", _conceptos(row)) if si)


# Dos fórmulas de total conviven en `detalle_facturacion`, y el recálculo tiene que
# guardar el total como venía:
#   * facturación (carga y edición):  (h + g + a − coseguro) × cantidad × sesión
#   * validaciones (`validaciones/core/grabado.py`): (h + g + a) × cantidad × sesión − coseguro
#     (el coseguro se descuenta una sola vez)
# Con cantidad·sesión = 1 o coseguro 0 dan lo mismo.
FORMULA_FACTURACION = "facturacion"
FORMULA_VALIDACION = "validacion"
_TOLERANCIA = Decimal("0.02")


def _total(formula: str, h, g, a, coseguro, cantidad: int, sesion: int) -> Decimal:
    if formula == FORMULA_VALIDACION:
        return quantize_money(
            service.calcular_importe_total(h, g, a, cantidad, sesion) - service._dec(coseguro)
        )
    return service.calcular_importe_total(h, g, a, cantidad, sesion, coseguro=coseguro)


def formula_de(row: DetalleFacturacionCMC) -> Optional[str]:
    """La fórmula con la que se calculó el total guardado, o None si ninguna lo
    reproduce (total tocado a mano o dato importado): esa fila no se recotiza."""
    h, g, a, cos, total = _montos_actuales(row)
    cant, ses = row.cantidad or 1, row.sesion or 1
    calza = {
        f for f in (FORMULA_FACTURACION, FORMULA_VALIDACION)
        if abs(_total(f, h, g, a, cos, cant, ses) - total) <= _TOLERANCIA
    }
    if not calza:
        return None
    if len(calza) == 1:
        return calza.pop()
    # Las dos dan lo mismo: decide de dónde vino la fila.
    return FORMULA_VALIDACION if row.validacion_estado else FORMULA_FACTURACION


def _montos_actuales(row: DetalleFacturacionCMC) -> tuple[Decimal, ...]:
    return tuple(service._dec(getattr(row, c) or 0) for c in (
        "honorarios", "gastos", "ayudante", "coseguro", "importe_total",
    ))


@contextlib.contextmanager
def memo_por_corrida(db: AsyncSession):
    """Mientras dura, lo que el lookup consulta sólo por código y O.S. se resuelve
    una vez (ver `nomenclador/service.py::_memo_por_corrida`). Se apaga al salir,
    así no queda nada en la sesión para el resto del request."""
    db.info[service_nm.MEMO_COTIZACION] = {}
    try:
        yield
    finally:
        db.info.pop(service_nm.MEMO_COTIZACION, None)


class CacheCotizacion:
    """Memoria de una corrida: cotiza una sola vez cada combinación que da el mismo
    precio, y las demás filas reusan el resultado.

    Una factura grande repite mucho médico + código (Sancor 09/2026: 3.116 filas,
    719 combinaciones), y cada cotización son ~10 consultas. La fecha sólo cambia
    el resultado cuando cruza un corte de vigencia —del precio
    (`nm_historial_precio_codigo`) o de una habilitación por médico
    (`nm_medico_codigo_habilitado`)— o cuando es futura, así que dos fechas entre
    los mismos cortes cotizan igual: la clave usa el *tramo*, no la fecha.

    Guarda también los errores, para que una fila repetida falle con el mismo
    motivo sin volver a consultar.
    """

    def __init__(self, cortes: Optional[list[datetime.date]] = None):
        # Sin cortes (una fila suelta) la clave es la fecha exacta: siempre correcto.
        self._cortes = sorted(set(cortes)) if cortes is not None else None
        self._prestadores: dict = {}
        self._precios: dict = {}

    @classmethod
    async def para(cls, db: AsyncSession, cod_obra: str) -> "CacheCotizacion":
        H, MH = HistorialPrecioCodigo, MedicoCodigoHabilitado
        un_dia = datetime.timedelta(days=1)
        cortes: list[datetime.date] = [datetime.date.today() + un_dia]  # fecha futura
        os_nro = service._cod_obra_to_int(cod_obra)
        for desde, hasta in (await db.execute(
            select(H.vigencia_desde, H.vigencia_hasta).where(H.obra_social_nro == os_nro).distinct()
        )).all():
            cortes.append(desde)
            if hasta is not None:
                cortes.append(hasta + un_dia)
        for desde, hasta in (await db.execute(
            select(MH.vigencia_desde, MH.vigencia_hasta).distinct()
        )).all():
            if desde is not None:
                cortes.append(desde)
            if hasta is not None:
                cortes.append(hasta + un_dia)
        return cls(cortes)

    def tramo(self, fecha: datetime.date):
        if self._cortes is None:
            return fecha
        return bisect.bisect_right(self._cortes, fecha)

    async def _memo(self, tabla: dict, clave, crear):
        if clave not in tabla:
            try:
                tabla[clave] = (await crear(), None)
            except Exception as e:  # noqa: BLE001 — se repite igual en la próxima fila
                tabla[clave] = (None, e)
        valor, error = tabla[clave]
        if error is not None:
            raise error
        return valor

    async def prestador(self, db, seleccion):
        return await self._memo(self._prestadores, seleccion, lambda: service.resolver_prestador(
            db, *seleccion, validar_clinica=False,
        ))

    async def precio(self, db, row, medico, fecha, via, ignorar_ventana):
        clave = (medico.ID, row.cod_obr, row.cod_nom, via, self.tramo(fecha), ignorar_ventana)
        return await self._memo(self._precios, clave, lambda: service.resolver_precio(
            db, row.cod_obr, medico, row.cod_nom, fecha, via=via, ignorar_ventana=ignorar_ventana,
        ))


async def recotizar_fila(
    db: AsyncSession, row: DetalleFacturacionCMC, *, ignorar_ventana: bool = True,
    cache: Optional[CacheCotizacion] = None,
) -> Recotizacion:
    if (row.manual or "").upper() != "A":
        return Recotizacion(estado="omitida", motivo=MOTIVO_MANUAL)

    con_h, con_g, con_a = _conceptos(row)

    formula = formula_de(row)
    if formula is None:
        return Recotizacion(estado="omitida", motivo=MOTIVO_TOTAL_RARO)

    fecha = service.fecha_para_precio(row.fecha_practica)
    via = row.via or service_vias.VIA_TRADICIONAL
    cache = cache or CacheCotizacion()
    es_pediatra = (row.tpo_funcion or "").upper() == service.TPO_FUNCION_PEDIATRA
    # Un ayudante de equipo cotiza con el médico de cabecera (ver
    # `service.medico_de_la_cabeza`).
    cotiza = row
    if con_a and not es_pediatra and row.grupo_equipo_id not in (None, row.id_detalle_prestaciones):
        cotiza = await db.get(DetalleFacturacionCMC, row.grupo_equipo_id) or row
    prestador = await cache.prestador(db, service.seleccion_prestador_de(cotiza))
    precio = await cache.precio(db, row, prestador.medico, fecha, via, ignorar_ventana)
    if not precio.admitido:
        return Recotizacion(estado="omitida", fecha=fecha, motivo=precio.motivo or "No admitido")
    if precio.sin_precio:
        return Recotizacion(
            estado="omitida", fecha=fecha, sin_precio=True,
            motivo=precio.motivo or "Sin precio vigente",
        )
    if precio.por_presupuesto:
        return Recotizacion(estado="omitida", fecha=fecha, motivo=MOTIVO_PRESUPUESTO)

    hb = precio.honorarios if con_h else CERO
    gb = precio.gastos if con_g else CERO
    ab = precio.ayudante if con_a else CERO
    h, g, a = service._aplicar_porcentaje(hb, gb, ab, row.porc or 100)

    if es_pediatra or con_a:
        # El coseguro es del acto: lo lleva la fila del médico, nunca la del
        # pediatra ni la del ayudante.
        coseguro = CERO
    elif row.ga_id is not None:
        # Vino de una validación: el coseguro es lo que el afiliado pagó según la
        # obra social, no el sugerido del nomenclador.
        coseguro = service._dec(row.coseguro or 0)
    else:
        coseguro = service._dec(precio.coseguro or 0)
    coseguro = min(coseguro, h + g + a)
    total = _total(formula, h, g, a, coseguro, row.cantidad or 1, row.sesion or 1)

    if h + g + a == 0 and service._dec(row.importe_total or 0) > 0:
        # El valor vigente está cargado en $0: es casi seguro un precio sin
        # completar, no una práctica que dejó de valer. No se pisa lo que había.
        return Recotizacion(estado="omitida", fecha=fecha, motivo=MOTIVO_PRECIO_CERO)

    nuevo = (h, g, a, coseguro, total)
    igual = not row.sin_valorizar and tuple(map(service._dec, nuevo)) == _montos_actuales(row)
    return Recotizacion(
        estado="igual" if igual else "cambia", fecha=fecha,
        honorarios=h, gastos=g, ayudante=a, coseguro=coseguro, importe_total=total,
        tpo_funcion=service.tpo_funcion_de(h, g, a, service.ROL_PEDIATRA if es_pediatra else None),
        snapshot=precio.snapshot,
    )


def aplicar(row: DetalleFacturacionCMC, r: Recotizacion) -> None:
    """Escribe en la fila lo que dio `recotizar_fila` (sólo si cambia)."""
    if r.estado != "cambia":
        return
    row.honorarios, row.gastos, row.ayudante = r.honorarios, r.gastos, r.ayudante
    row.coseguro = r.coseguro
    row.importe_total = r.importe_total
    row.tpo_funcion = r.tpo_funcion
    row.calculo_snapshot = r.snapshot
    row.sin_valorizar = None


# ── Recálculo de una factura ─────────────────────────────────────────────────

class RecalculoIn(BaseModel):
    cod_obra: str
    periodo: str = Field(..., pattern=r"^\d{6}$")
    # None = todas las prestaciones de la factura; con código, sólo ese código.
    codigo: Optional[str] = None
    dry_run: bool = True


class RecalculoFila(BaseModel):
    id: int
    cod_medico: str
    cod_nomenclador: str
    fecha_cotizacion: Optional[datetime.date] = None
    grupo_equipo_id: Optional[int] = None
    publicado: bool = False
    honorarios_antes: Decimal
    gastos_antes: Decimal
    ayudante_antes: Decimal
    coseguro_antes: Decimal
    importe_antes: Decimal
    honorarios: Decimal
    gastos: Decimal
    ayudante: Decimal
    coseguro: Decimal
    importe_despues: Decimal
    diferencia: Decimal


class RecalculoOmitida(BaseModel):
    id: int
    cod_medico: str
    cod_nomenclador: str
    fecha_practica: Optional[datetime.date] = None
    motivo: str


class RecalculoOut(BaseModel):
    dry_run: bool
    cod_obra: str
    periodo: str
    codigo: Optional[str] = None
    version: int
    total: int
    cambian: int
    sin_cambios: int
    omitidas: int
    publicadas_afectadas: int
    importe_antes: Decimal
    importe_despues: Decimal
    diferencia: Decimal
    filas: List[RecalculoFila] = Field(default_factory=list)
    omitidas_detalle: List[RecalculoOmitida] = Field(default_factory=list)


async def recalcular_precios(db: AsyncSession, body: RecalculoIn) -> RecalculoOut:
    """Recotiza las prestaciones de una factura abierta. Con `dry_run` sólo informa."""
    with memo_por_corrida(db):
        return await _recalcular_precios(db, body)


async def _recalcular_precios(db: AsyncSession, body: RecalculoIn) -> RecalculoOut:
    cabecera = await service._get_factura(db, body.cod_obra, body.periodo)
    if cabecera is None:
        raise HTTPException(404, "No hay factura para esa obra social y período")
    if cabecera.estado != service.FACTURA_ESTADO_ABIERTA:
        raise HTTPException(409, "La factura está cerrada o liquidada: no se pueden recalcular precios")

    # Se lee antes del commit/rollback: después la cabecera queda vencida.
    version = cabecera.version
    codigo = (body.codigo or "").strip() or None
    D = DetalleFacturacionCMC
    stmt = select(D).where(
        D.cod_obr == body.cod_obra, D.periodo == body.periodo,
        D.version == version, D.estado == "A",
    )
    if codigo:
        stmt = stmt.where(D.cod_nom == codigo)
    filas = list((await db.execute(stmt.order_by(D.id_detalle_prestaciones))).scalars())
    cache = await CacheCotizacion.para(db, body.cod_obra)

    cambian: list[RecalculoFila] = []
    omitidas: list[RecalculoOmitida] = []
    sin_cambios = 0
    antes_total = despues_total = CERO

    for row in filas:
        antes = _montos_actuales(row)
        antes_total += antes[4]
        try:
            r = await recotizar_fila(db, row, cache=cache)
        except Exception as e:  # noqa: BLE001 — se informa por fila, no corta el resto
            r = Recotizacion(estado="omitida", motivo=str(getattr(e, "detail", e)))
        if r.estado == "omitida":
            despues_total += antes[4]
            omitidas.append(RecalculoOmitida(
                id=row.id_detalle_prestaciones, cod_medico=str(row.cod_med),
                cod_nomenclador=str(row.cod_nom or ""), fecha_practica=row.fecha_practica,
                motivo=r.motivo or "",
            ))
            continue
        despues_total += r.importe_total
        if r.estado == "igual":
            sin_cambios += 1
            continue
        cambian.append(RecalculoFila(
            id=row.id_detalle_prestaciones, cod_medico=str(row.cod_med),
            cod_nomenclador=str(row.cod_nom or ""), fecha_cotizacion=r.fecha,
            grupo_equipo_id=row.grupo_equipo_id, publicado=bool(row.publicado),
            honorarios_antes=antes[0], gastos_antes=antes[1], ayudante_antes=antes[2],
            coseguro_antes=antes[3], importe_antes=antes[4],
            honorarios=r.honorarios, gastos=r.gastos, ayudante=r.ayudante,
            coseguro=r.coseguro, importe_despues=r.importe_total,
            diferencia=r.importe_total - antes[4],
        ))
        if not body.dry_run:
            aplicar(row, r)

    if body.dry_run:
        await db.rollback()
    else:
        await db.commit()

    return RecalculoOut(
        dry_run=body.dry_run, cod_obra=body.cod_obra, periodo=body.periodo, codigo=codigo,
        version=version, total=len(filas), cambian=len(cambian),
        sin_cambios=sin_cambios, omitidas=len(omitidas),
        publicadas_afectadas=sum(1 for f in cambian if f.publicado),
        importe_antes=antes_total, importe_despues=despues_total,
        diferencia=despues_total - antes_total,
        filas=cambian, omitidas_detalle=omitidas,
    )
