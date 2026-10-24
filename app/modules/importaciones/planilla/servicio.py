"""Importación de prestaciones desde la planilla del Colegio.

## En qué se diferencia de Prevención, Swiss y UNNE

Esos tres leen el reporte que manda una obra social. Esta lee una planilla armada
por el propio Colegio (columnas en `columnas.py`, plantilla descargable), de modo que
trae de todo y nada está validado de antemano: una fila puede traer un socio que no
existe, un código que la obra social no tiene o una fecha imposible.

Por eso **cada fila pasa por las mismas reglas que la carga manual**:
`facturacion.service.preparar_equipo` es la función que valida y cotiza el formulario
de «Cargar prestaciones», y es la que se llama acá. Una fila se rechaza por lo mismo
por lo que el formulario la rechazaría, con el mismo mensaje.

## El camino

    leer la fila (tipos, enumerados, fechas, importes)   → si no, omitida
    socio por NRO_SOCIO; MATRICULA como control          → si no coincide, elegir_socio
    agrupar por EQUIPO (cabeza = ROL MEDICO)
    preparar_equipo (valida y cotiza, sin escribir)      → si no, omitida
    ya cargada en la obra social + período               → duplicada
    todo OK                                              → grabable / grabada

**Un equipo entra entero o no entra:** si una fila del equipo falla, ninguna de sus
filas se graba.

## Todo o nada

`previsualizar` no escribe nada. `confirmar` agrega todas las filas a la sesión y hace
**un solo commit** al final; cualquier error antes de ese commit las descarta todas.
La cancelación del operador se mira (`cancelado`, ver `estado.py`) al empezar, en cada
paso y una vez más justo antes del commit. Cada fila grabada lleva el lote en
`validacion_detalle`, así se puede contar después qué quedó grabado (`estado_lote`).
"""
import datetime
import logging
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Awaitable, Callable, Optional

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.money import quantize_money
from app.db.models import Afiliado, DetalleFacturacionCMC, ListadoMedico, NomencladorCMC, ObrasSociales
from app.modules.facturacion import historial, service as fact
from app.modules.facturacion.schemas import PrestacionItem
from app.modules.importaciones import nucleo
from app.modules.importaciones.planilla.columnas import CALCULOS, ROLES, TIPOS, VIAS
from app.modules.importaciones.planilla.schemas import (
    FilaPlanilla,
    FilaPlanillaResultado,
    LoteOut,
    PlanillaIn,
    PeriodosPlanillaOut,
    PlanillaOut,
    ResumenPlanilla,
)
from app.modules.importaciones.schemas import PeriodoOpcion

log = logging.getLogger(__name__)

CERO = Decimal("0.00")

GRABABLE = nucleo.GRABABLE
GRABADA = nucleo.GRABADA
DUPLICADA = nucleo.DUPLICADA
OMITIDA = nucleo.OMITIDA
ELEGIR_SOCIO = nucleo.ELEGIR_SOCIO
DESCARTADA = "descartada"

# El TIPO de la planilla → el `tipo` que guarda el sistema.
TIPO_DE_PLANILLA = {
    "CONSULTA": fact.TIPO_CONSULTA,
    "PRACTICA": fact.TIPO_PRACTICA,
    "HONORARIOS INDIVIDUALES": fact.CATEGORIA_HONORARIOS_INDIVIDUALES,
    "SANATORIO": fact.TIPO_SANATORIO,
}

PREFIJO_LOTE = "Importado de planilla · lote "

Cancelado = Callable[[], Awaitable[bool]]
# (fase, hecho, total): fase es «validando», «grabando» o «guardando».
Progreso = Callable[[str, int, int], None]


class ImportacionCancelada(Exception):
    """El operador canceló antes del commit: no se grabó nada."""


# ── Lectura de celdas ────────────────────────────────────────────────────────


def _norm(valor: str) -> str:
    """Mayúsculas sin acentos ni espacios de más: «Práctica » → «PRACTICA»."""
    sin = unicodedata.normalize("NFD", valor or "")
    sin = "".join(c for c in sin if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", sin).strip().upper()


def _entero(valor: str, nombre: str, *, minimo: int = 1) -> Optional[int]:
    """`None` si está vacío; `ValueError` con el motivo si no es un entero válido."""
    v = (valor or "").strip()
    if not v:
        return None
    try:
        d = Decimal(v.replace(",", "."))
    except InvalidOperation:
        raise ValueError(f"{nombre} «{v}» no es un número.")
    if d != d.to_integral_value():
        raise ValueError(f"{nombre} «{v}» tiene que ser un número entero.")
    n = int(d)
    if n < minimo:
        raise ValueError(f"{nombre} tiene que ser {minimo} o más.")
    return n


def _importe(valor: str, nombre: str) -> Decimal:
    """Importe ≥ 0; vacío vale 0. Coma decimal o punto."""
    v = (valor or "").strip().replace(" ", "").replace("$", "")
    if not v:
        return CERO
    if "," in v:
        v = v.replace(".", "").replace(",", ".")
    try:
        d = Decimal(v)
    except InvalidOperation:
        raise ValueError(f"{nombre} «{valor.strip()}» no es un importe.")
    if d < 0:
        raise ValueError(f"{nombre} no puede ser negativo.")
    return quantize_money(d)


_FECHA_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")
_FECHA_LATINA = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})")


def _fecha(valor: str) -> Optional[datetime.date]:
    v = (valor or "").strip()
    if not v or v == "-":
        return None
    m = _FECHA_ISO.match(v)
    if m:
        anio, mes, dia = (int(x) for x in m.groups())
    else:
        m = _FECHA_LATINA.match(v)
        if not m:
            raise ValueError(f"FECHA «{v}» no es una fecha (usá dd/mm/aaaa).")
        dia, mes, anio = (int(x) for x in m.groups())
    try:
        return datetime.date(anio, mes, dia)
    except ValueError:
        raise ValueError(f"FECHA «{v}» no es una fecha válida.")


# ── Una fila ya leída ────────────────────────────────────────────────────────


@dataclass
class _Fila:
    indice: int
    f: FilaPlanilla
    r: FilaPlanillaResultado
    # Leídos de la celda (None hasta que `_leer` los complete).
    tipo: str = ""                       # el `tipo` del sistema que declara la planilla
    rol: str = "MEDICO"
    equipo: str = ""
    nro_socio: Optional[int] = None
    clinica: Optional[int] = None
    fecha: Optional[datetime.date] = None
    cantidad: int = 1
    sesion: int = 1
    porcentaje: int = 100
    calculo: str = "A"
    honorarios: Decimal = CERO
    gastos: Decimal = CERO
    ayudante: Decimal = CERO
    coseguro: Optional[Decimal] = None
    socio: Optional[ListadoMedico] = None
    item: Optional[PrestacionItem] = None
    error: str = ""


@dataclass
class _Grupo:
    clave: str
    filas: list[_Fila]
    prep: Optional[fact.EquipoPreparado] = None
    resultado: str = GRABABLE
    ids: list[int] = field(default_factory=list)


def _leer(fila: _Fila) -> None:
    """Pasa las celdas a tipos. Deja el primer problema en `fila.error`."""
    f = fila.f
    try:
        tipo = _norm(f.tipo)
        if tipo not in TIPOS:
            raise ValueError(
                f"TIPO «{f.tipo.strip()}» no es válido (CONSULTA, PRACTICA, HONORARIOS INDIVIDUALES o SANATORIO)."
                if tipo else "Falta el TIPO."
            )
        fila.tipo = TIPO_DE_PLANILLA[tipo]

        fila.nro_socio = _entero(f.nro_socio, "NRO_SOCIO")
        if fila.nro_socio is None and not f.nro_socio_elegido:
            raise ValueError("Falta el NRO_SOCIO.")

        if not f.codigo.strip():
            raise ValueError("Falta el CODIGO.")

        cantidad = _entero(f.cantidad, "CANTIDAD")
        if cantidad is None:
            raise ValueError("Falta la CANTIDAD.")
        fila.cantidad = cantidad
        fila.sesion = _entero(f.sesion, "SESION") or 1
        fila.porcentaje = _entero(f.porcentaje, "PORCENTAJE") or 100
        fila.clinica = _entero(f.clinica, "CLINICA")
        fila.fecha = _fecha(f.fecha)

        via = _norm(f.via)
        if via and via not in VIAS:
            raise ValueError(f"VIA «{f.via.strip()}» no es válida (T o L).")
        calculo = _norm(f.calculo) or "A"
        if calculo not in CALCULOS:
            raise ValueError(f"CALCULO «{f.calculo.strip()}» no es válido (A o M).")
        fila.calculo = calculo
        rol = _norm(f.rol) or "MEDICO"
        if rol not in ROLES:
            raise ValueError(f"ROL «{f.rol.strip()}» no es válido (MEDICO, AYUDANTE o PEDIATRA).")
        fila.rol = rol
        fila.equipo = f.equipo.strip()

        fila.honorarios = _importe(f.honorarios, "HONORARIOS")
        fila.gastos = _importe(f.gastos, "GASTOS")
        fila.ayudante = _importe(f.ayudante, "AYUDANTE")
        fila.coseguro = _importe(f.coseguro, "COSEGURO") if f.coseguro.strip() else None

        if len(f.autorizacion.strip()) > 30:
            raise ValueError("La AUTORIZACION tiene más de 30 caracteres.")
        if len(f.nro_afiliado.strip()) > 20:
            raise ValueError("El NRO_AFILIADO tiene más de 20 caracteres.")

        _reglas_de_columnas(fila)
    except ValueError as e:
        fila.error = str(e)


def _reglas_de_columnas(fila: _Fila) -> None:
    """Combinaciones de columnas que no tienen sentido, antes de tocar la base."""
    if fila.rol in ("AYUDANTE", "PEDIATRA") and not fila.equipo:
        raise ValueError(f"ROL {fila.rol} necesita un EQUIPO.")
    if fila.tipo == fact.TIPO_SANATORIO and fila.clinica is None:
        raise ValueError("En un SANATORIO falta la CLINICA.")
    if fila.tipo in (fact.TIPO_CONSULTA, fact.TIPO_PRACTICA) and fila.clinica is not None:
        raise ValueError(
            "La CLINICA va sólo en SANATORIO u HONORARIOS INDIVIDUALES; en CONSULTA y PRACTICA se deja vacía."
        )
    h, g, a = fila.honorarios, fila.gastos, fila.ayudante
    if a > 0 and (h > 0 or g > 0):
        raise ValueError("Una fila no puede llevar AYUDANTE junto con HONORARIOS o GASTOS.")
    if a > 0 and (fila.rol in ("MEDICO", "PEDIATRA") and fila.equipo):
        raise ValueError("El importe AYUDANTE va sólo en la fila del ayudante (ROL AYUDANTE).")
    if fila.rol == "AYUDANTE" and a <= 0:
        raise ValueError("La fila del ayudante tiene que informar el importe en AYUDANTE.")
    if fila.rol == "PEDIATRA" and h <= 0:
        raise ValueError("La fila del pediatra tiene que informar sus HONORARIOS.")
    if h <= 0 and g <= 0 and a <= 0:
        raise ValueError(
            "La fila no trae ningún importe: indicá HONORARIOS, GASTOS o AYUDANTE "
            "(en CALCULO A sólo marcan qué concepto se factura)."
        )


# ── Socios ───────────────────────────────────────────────────────────────────


def _resolver_socio(
    fila: _Fila,
    socios: dict[int, ListadoMedico],
    por_matricula: dict[int, list[ListadoMedico]],
) -> None:
    """Fija `fila.socio`, o deja la fila en `elegir_socio` / con error.

    El NRO_SOCIO manda; la MATRICULA es un control. Si no coinciden no se adivina:
    el operador elige entre el socio indicado y los que tienen esa matrícula.
    """
    f = fila.f
    mat = nucleo.matricula_int(f.matricula)
    elegido = f.nro_socio_elegido

    if elegido:
        socio = socios.get(elegido)
        if socio is None:
            fila.error = f"El socio elegido ({elegido}) no existe."
            return
        fila.nro_socio = elegido
        fila.socio = socio
        return

    socio = socios.get(fila.nro_socio or 0)
    lista = por_matricula.get(mat, []) if mat is not None else []

    if socio is None:
        if lista:
            _pedir_eleccion(fila, lista, f"No existe el socio {fila.nro_socio}: elegí cuál es.")
        else:
            fila.error = f"No existe el socio {fila.nro_socio}."
        return

    if mat is not None and socio.MATRICULA_PROV != mat:
        candidatos = [socio] + [m for m in lista if m.NRO_SOCIO != socio.NRO_SOCIO]
        _pedir_eleccion(
            fila, candidatos,
            f"El socio {socio.NRO_SOCIO} tiene la matrícula {socio.MATRICULA_PROV}, no la {mat}: elegí cuál es.",
        )
        return

    fila.socio = socio


def _pedir_eleccion(fila: _Fila, candidatos: list[ListadoMedico], motivo: str) -> None:
    fila.r.resultado = ELEGIR_SOCIO
    fila.r.motivo = motivo
    fila.r.candidatos = nucleo.candidatos_out(candidatos)


# ── Armado del ítem ──────────────────────────────────────────────────────────


def _armar_item(fila: _Fila, socios: dict[int, ListadoMedico]) -> None:
    """Traduce las columnas al `PrestacionItem` del formulario de carga."""
    f = fila.f
    socio_nro = str(fila.socio.NRO_SOCIO)

    cod_medico: str
    ejecutor: Optional[str] = None
    cod_clinica: Optional[int] = None
    if fila.tipo == fact.TIPO_SANATORIO:
        org = socios.get(fila.clinica or 0)
        if org is None or not org.es_organizacion:
            fila.error = f"El código {fila.clinica} no es una clínica."
            return
        cod_medico, ejecutor = str(org.NRO_SOCIO), socio_nro
    else:
        cod_medico, cod_clinica = socio_nro, fila.clinica

    # Concepto: en manual se mandan los importes; en automático sólo cuenta qué
    # concepto es > 0 (el importe lo pone la cotización vigente).
    h, g, a = fila.honorarios, fila.gastos, fila.ayudante
    try:
        fila.item = PrestacionItem(
            cod_medico=cod_medico,
            cod_medico_ejecutor=ejecutor,
            cod_clinica=cod_clinica,
            fecha_practica=fila.fecha,
            autorizacion=f.autorizacion.strip() or None,
            dni_paciente=f.nro_afiliado.strip() or None,
            nombre_paciente=f.afiliado.strip()[:100] or None,
            cod_nomenclador=f.codigo.strip(),
            via=_norm(f.via) or None,
            cantidad=fila.cantidad,
            sesion=fila.sesion,
            tipo_calculo=fila.calculo,
            honorarios=h,
            gastos=g,
            ayudante=a,
            porcentaje=fila.porcentaje,
            coseguro=fila.coseguro,
            rol=fact.ROL_PEDIATRA if fila.rol == "PEDIATRA" else None,
        )
    except ValidationError as e:
        fila.error = "; ".join(err["msg"] for err in e.errors())


def _detalle(e: HTTPException) -> str:
    d = e.detail
    return d if isinstance(d, str) else str(d.get("mensaje") if isinstance(d, dict) and d.get("mensaje") else d)


# ── Repetidas ────────────────────────────────────────────────────────────────


def _paciente_key(dni: Optional[str], nombre: Optional[str]) -> str:
    return _norm(dni or nombre or "")


def _clave(row: DetalleFacturacionCMC) -> tuple:
    return (
        str(row.cod_med), row.cod_nom, row.fecha_practica,
        (row.autorizacion or "").strip(), _paciente_key(row.dni_p, row.nom_ape_p),
    )


def _identificable(row: DetalleFacturacionCMC) -> bool:
    """Sin autorización ni fecha no hay con qué reconocer una prestación repetida."""
    return bool((row.autorizacion or "").strip()) or row.fecha_practica is not None


async def _ya_cargadas(db: AsyncSession, cod_obra: str, periodo: str) -> dict[tuple, list[int]]:
    M = DetalleFacturacionCMC
    filas = (await db.execute(
        select(M.id_detalle_prestaciones, M.cod_med, M.cod_nom, M.fecha_practica, M.autorizacion, M.dni_p, M.nom_ape_p)
        .where(M.cod_obr == cod_obra, M.periodo == periodo, M.estado != nucleo.DETALLE_FUERA_DE_FACTURA)
    )).all()
    out: dict[tuple, list[int]] = {}
    for id_, med, cod, fec, aut, dni, nom in filas:
        out.setdefault((str(med), cod, fec, (aut or "").strip(), _paciente_key(dni, nom)), []).append(id_)
    return out


# ── Procesamiento ────────────────────────────────────────────────────────────


async def _revisar(cancelado: Optional[Cancelado]) -> None:
    if cancelado is not None and await cancelado():
        raise ImportacionCancelada()


async def procesar(
    db: AsyncSession,
    *,
    datos: PlanillaIn,
    usuario: str,
    grabar: bool,
    cancelado: Optional[Cancelado] = None,
    progreso: Optional[Progreso] = None,
) -> PlanillaOut:
    cod_obra = str(datos.obra_social)

    nombre_os = (await db.execute(
        select(ObrasSociales.OBRA_SOCIAL).where(ObrasSociales.NRO_OBRASOCIAL == datos.obra_social).limit(1)
    )).scalar_one_or_none()
    if nombre_os is None:
        raise HTTPException(422, f"La obra social {datos.obra_social} no existe.")
    await fact.exigir_obra_social_activa(db, cod_obra)

    periodo = await fact.resolver_periodo_colegio_carga(db, cod_obra, datos.periodo)
    cabecera = await fact._get_factura(db, cod_obra, periodo)
    fact._gate_carga(cabecera, fact.ORIGEN_COLEGIO)
    version_destino = cabecera.version if cabecera is not None else 1

    # ── 1. Leer las filas ──
    filas: list[_Fila] = []
    for i, f in enumerate(datos.filas):
        r = FilaPlanillaResultado(
            indice=i, fila=f.fila or i + 2, resultado=OMITIDA, codigo=f.codigo.strip(),
            afiliado=(f.afiliado.strip() or f.nro_afiliado.strip()), matricula=f.matricula.strip(),
            comentarios=f.comentarios.strip(), equipo=f.equipo.strip(), rol=_norm(f.rol),
        )
        fila = _Fila(indice=i, f=f, r=r)
        if f.descartada:
            r.resultado = DESCARTADA
            r.motivo = "Descartada por el operador."
        else:
            _leer(fila)
            r.cantidad = fila.cantidad
            r.fecha = fila.fecha
        filas.append(fila)
    vivas = [x for x in filas if x.r.resultado != DESCARTADA]

    # ── 2. Socios ──
    pedidos = {x.nro_socio for x in vivas if x.nro_socio} | {x.f.nro_socio_elegido for x in vivas if x.f.nro_socio_elegido}
    pedidos |= {x.clinica for x in vivas if x.clinica}
    socios = await nucleo.socios_por_nro(db, {n for n in pedidos if n})
    mats = {m for m in (nucleo.matricula_int(x.f.matricula) for x in vivas) if m is not None}
    por_matricula = await nucleo.candidatos_por_matricula(db, mats)
    for x in vivas:
        if x.error:
            continue
        _resolver_socio(x, socios, por_matricula)
        if x.socio is not None:
            x.r.nro_socio = x.socio.NRO_SOCIO
            x.r.medico = (x.socio.NOMBRE or "").strip()
            _armar_item(x, socios)

    # Pacientes con número que no están en el padrón: se registran al confirmar.
    nuevos = await _afiliados_nuevos(db, vivas)

    # ── 3. Grupos ──
    grupos: list[_Grupo] = []
    por_clave: dict[str, _Grupo] = {}
    for x in vivas:
        if x.equipo:
            g = por_clave.get(_norm(x.equipo))
            if g is None:
                g = por_clave[_norm(x.equipo)] = _Grupo(clave=x.equipo, filas=[])
                grupos.append(g)
            g.filas.append(x)
        else:
            grupos.append(_Grupo(clave="", filas=[x]))

    hecho = 0
    for g in grupos:
        await _revisar(cancelado)
        await _preparar_grupo(
            db, g, cod_obra=cod_obra, periodo=periodo, version=version_destino, usuario=usuario, nuevos=nuevos,
        )
        hecho += len(g.filas)
        if progreso is not None:
            progreso("validando", hecho, len(vivas))

    # ── 4. Repetidas ──
    existentes = await _ya_cargadas(db, cod_obra, periodo)
    pendientes = Counter({k: len(v) for k, v in existentes.items()})
    vistas: set[tuple] = set()
    for g in grupos:
        if g.resultado != GRABABLE or g.prep is None:
            continue
        _marcar_repetidas(g, existentes, pendientes, vistas)

    # ── 5. Descripciones ──
    codigos = {x.f.codigo.strip() for x in vivas if x.f.codigo.strip()}
    descripciones: dict[str, str] = {}
    if codigos:
        descripciones = {
            c: (d or "").strip() for c, d in (await db.execute(
                select(NomencladorCMC.codigo, NomencladorCMC.descripcion).where(NomencladorCMC.codigo.in_(codigos))
            )).all()
        }
    for x in filas:
        x.r.descripcion = descripciones.get(x.r.codigo, "")

    resultados = [x.r for x in filas]

    # ── 6. Grabar ──
    if grabar:
        await _grabar(db, grupos, resultados, cod_obra=cod_obra, periodo=periodo, usuario=usuario,
                      lote=datos.lote, cancelado=cancelado, progreso=progreso, nuevos=nuevos)

    return PlanillaOut(
        resumen=_resumir(resultados, grupos, datos.obra_social, nombre_os, periodo, nuevos),
        filas=resultados,
    )


async def _afiliados_nuevos(db: AsyncSession, filas: list[_Fila]) -> dict[str, Optional[str]]:
    """número → nombre de los pacientes de la planilla que no están en el padrón. El nombre es
    el de la primera fila que lo trae (puede faltar: el padrón admite sólo el número)."""
    por_dni: dict[str, Optional[str]] = {}
    for x in filas:
        if x.item is None or x.error or not x.item.dni_paciente:
            continue
        nombre = x.f.afiliado.strip()[:100] or None
        if x.item.dni_paciente not in por_dni or (por_dni[x.item.dni_paciente] is None and nombre):
            por_dni[x.item.dni_paciente] = nombre
    if not por_dni:
        return {}
    existentes = set((await db.execute(select(Afiliado.dni).where(Afiliado.dni.in_(list(por_dni))))).scalars().all())
    return {dni: n for dni, n in por_dni.items() if dni not in existentes}


async def _preparar_grupo(
    db: AsyncSession, g: _Grupo, *, cod_obra: str, periodo: str, version: int, usuario: str,
    nuevos: Optional[dict[str, Optional[str]]] = None,
) -> None:
    """Valida y cotiza el grupo con las reglas de la carga manual. Deja el motivo en
    cada fila si no entra."""
    pendiente_eleccion = [x for x in g.filas if x.r.resultado == ELEGIR_SOCIO]
    con_error = [x for x in g.filas if x.error]
    if pendiente_eleccion or con_error:
        _rechazar(g, con_error, pendiente_eleccion)
        return

    try:
        items = _ordenar_equipo(g)
    except ValueError as e:
        _rechazar_todo(g, str(e))
        return

    try:
        g.prep = await fact.preparar_equipo(
            db, cod_obra=cod_obra, periodo=periodo, version_destino=version,
            items=[x.item for x in items], usuario=usuario, actor=fact.ORIGEN_COLEGIO,
            pacientes_nuevos=nuevos,
        )
    except HTTPException as e:
        _rechazar_todo(g, _detalle(e))
        return

    g.filas = items  # mismo orden que las filas preparadas
    cabeza = g.prep.filas[g.prep.cabeza_idx] if len(items) > 1 and g.prep.cabeza_idx is not None else None
    for n, (x, row) in enumerate(zip(g.filas, g.prep.filas)):
        # Los integrantes del equipo se graban con el tipo de la cabeza (`persistir_equipo`);
        # la previsualización tiene que mostrar el que van a tener.
        tipo = cabeza.tipo if cabeza is not None and n != g.prep.cabeza_idx and cabeza.tipo else row.tipo
        x.r.tipo = tipo or ""
        x.r.importe_total = row.importe_total or CERO
        if x.tipo != tipo:
            x.r.aviso = _unir(x.r.aviso, f"La planilla dice {x.tipo} y el sistema lo carga como {tipo}.")
        if not _identificable(row):
            x.r.aviso = _unir(x.r.aviso, "Sin autorización ni fecha: no se puede detectar si ya estaba cargada.")
        elif row.fecha_practica is None:
            x.r.aviso = _unir(x.r.aviso, "Sin fecha de práctica.")
        if not (row.dni_p or row.nom_ape_p):
            x.r.aviso = _unir(x.r.aviso, "Sin paciente.")
        if nuevos and row.dni_p in nuevos:
            x.r.aviso = _unir(x.r.aviso, f"El afiliado {row.dni_p} no está en el padrón: se registra al importar.")
        x.r.resultado = GRABABLE
    if len(g.filas) > 1:
        pacientes = {_paciente_key(r.dni_p, r.nom_ape_p) for r in g.prep.filas}
        fechas = {r.fecha_practica for r in g.prep.filas}
        if len(pacientes) > 1 or len(fechas) > 1:
            g.filas[0].r.aviso = _unir(g.filas[0].r.aviso, "Los integrantes del equipo tienen distinto paciente o fecha.")


def _ordenar_equipo(g: _Grupo) -> list[_Fila]:
    """El cirujano primero (es la cabeza del equipo); después el resto, en orden de archivo."""
    if len(g.filas) == 1:
        return list(g.filas)
    medicos = [x for x in g.filas if x.rol == "MEDICO"]
    if len(medicos) != 1:
        raise ValueError(
            f"El equipo {g.clave} necesita exactamente una fila con ROL MEDICO (el cirujano); tiene {len(medicos)}."
        )
    codigo = medicos[0].f.codigo.strip()
    for x in g.filas:
        if x.rol == "AYUDANTE" and x.f.codigo.strip() != codigo:
            raise ValueError(f"El ayudante de la fila {x.r.fila} tiene que llevar el mismo CODIGO que el cirujano ({codigo}).")
    return medicos + [x for x in g.filas if x.rol != "MEDICO"]


def _unir(a: str, b: str) -> str:
    return f"{a} {b}".strip() if a else b


def _rechazar(g: _Grupo, con_error: list[_Fila], pendientes: list[_Fila]) -> None:
    """Una fila con problema deja afuera a todo su equipo."""
    g.resultado = OMITIDA
    solo = len(g.filas) == 1
    for x in g.filas:
        if x.error:
            x.r.resultado = OMITIDA
            x.r.motivo = x.error
        elif x.r.resultado == ELEGIR_SOCIO:
            continue
        else:
            x.r.resultado = OMITIDA
            otra = (con_error + pendientes)[0].r.fila
            x.r.motivo = f"El equipo {g.clave} no entra: la fila {otra} tiene un problema."
    if pendientes and not solo:
        g.resultado = ELEGIR_SOCIO


def _rechazar_todo(g: _Grupo, motivo: str) -> None:
    g.resultado = OMITIDA
    solo = len(g.filas) == 1
    for x in g.filas:
        x.r.resultado = OMITIDA
        x.r.motivo = motivo if solo else f"Equipo {g.clave}: {motivo}"


def _marcar_repetidas(
    g: _Grupo, existentes: dict[tuple, list[int]], pendientes: Counter, vistas: set[tuple],
) -> None:
    claves = [_clave(row) if _identificable(row) else None for row in g.prep.filas]
    repetidas = [k is not None and pendientes[k] > 0 for k in claves]

    if any(repetidas) and not all(repetidas):
        _rechazar_todo(g, "ya está cargada una parte de este equipo; revisalo antes de importar.")
        return
    if all(repetidas):
        g.resultado = DUPLICADA
        for x, k in zip(g.filas, claves):
            pendientes[k] -= 1
            x.r.resultado = DUPLICADA
            x.r.motivo = f"Ya está cargada en el período (prestación {existentes[k][pendientes[k]]})."
        return
    for x, k in zip(g.filas, claves):
        if k is None:
            continue
        if k in vistas:
            x.r.aviso = _unir(x.r.aviso, "Se repite en la planilla (misma autorización, código, fecha y paciente).")
        vistas.add(k)


async def _grabar(
    db: AsyncSession,
    grupos: list[_Grupo],
    resultados: list[FilaPlanillaResultado],
    *,
    cod_obra: str,
    periodo: str,
    usuario: str,
    lote: Optional[str],
    cancelado: Optional[Cancelado],
    progreso: Optional[Progreso] = None,
    nuevos: Optional[dict[str, Optional[str]]] = None,
) -> None:
    sin_socio = sum(1 for r in resultados if r.resultado == ELEGIR_SOCIO)
    if sin_socio:
        raise HTTPException(
            422, f"Quedan {sin_socio} filas sin socio: elegilo o descartalas antes de confirmar.",
        )
    a_grabar = [g for g in grupos if g.resultado == GRABABLE and g.prep is not None]
    if not a_grabar:
        raise HTTPException(422, "No hay ninguna fila para importar.")

    # Los pacientes que no estaban en el padrón se registran en la misma transacción que las
    # prestaciones: si se cancela o falla algo, tampoco quedan.
    usados = {row.dni_p for g in a_grabar for row in g.prep.filas if nuevos and row.dni_p in nuevos}
    for dni in sorted(usados):
        db.add(Afiliado(dni=dni, nombre=nuevos[dni], usuario=usuario[:30]))
    if usados:
        await db.flush()

    total = sum(len(g.filas) for g in a_grabar)
    hecho = 0
    for g in a_grabar:
        await _revisar(cancelado)
        if lote:
            for x, row in zip(g.filas, g.prep.filas):
                row.validacion_detalle = f"{PREFIJO_LOTE}{lote} · fila {x.r.fila}"[:255]
        g.ids = await fact.persistir_equipo(
            db, g.prep, tipo_evento=historial.IMPORTADA, actor_tipo=historial.ACTOR_IMPORTACION,
            contexto_evento={"importador": "planilla", "lote": lote},
        )
        hecho += len(g.filas)
        if progreso is not None:
            progreso("grabando", hecho, total)

    await fact._ensure_factura_abierta(db, cod_obra, periodo, usuario)

    # Último punto de no retorno: si el operador canceló mientras se armaba, se descarta
    # todo lo agregado a la sesión y no se hace commit. Pasado este control ya no se puede
    # frenar, y el estado lo dice (`guardando`).
    await _revisar(cancelado)
    if progreso is not None:
        progreso("guardando", total, total)
    await db.commit()

    for g in a_grabar:
        for x, id_ in zip(g.filas, g.ids):
            x.r.resultado = GRABADA
            x.r.id_detalle = id_


def _resumir(
    resultados: list[FilaPlanillaResultado], grupos: list[_Grupo], obra_social: int, nombre_os: str, periodo: str,
    nuevos: dict[str, Optional[str]],
) -> ResumenPlanilla:
    entran = [r for r in resultados if r.resultado in (GRABABLE, GRABADA)]
    filas_que_entran = [
        row for g in grupos if g.resultado == GRABABLE and g.prep is not None for row in g.prep.filas
    ]
    return ResumenPlanilla(
        obra_social=obra_social,
        obra_social_nombre=(nombre_os or "").strip(),
        periodo=periodo,
        total=len(resultados),
        grabables=len(entran),
        duplicadas=sum(1 for r in resultados if r.resultado == DUPLICADA),
        omitidas=sum(1 for r in resultados if r.resultado == OMITIDA),
        descartadas=sum(1 for r in resultados if r.resultado == DESCARTADA),
        por_elegir=sum(1 for r in resultados if r.resultado == ELEGIR_SOCIO),
        con_aviso=sum(1 for r in entran if r.aviso),
        equipos=sum(1 for g in grupos if len(g.filas) > 1 and g.resultado in (GRABABLE, GRABADA) and g.prep is not None),
        importe_total=quantize_money(sum((r.importe_total for r in entran), CERO)),
        afiliados_nuevos=len({row.dni_p for row in filas_que_entran if row.dni_p in nuevos}),
        sin_autorizacion=sum(1 for row in filas_que_entran if not (row.autorizacion or "").strip()),
        no_verificables=sum(1 for row in filas_que_entran if not _identificable(row)),
    )


# ── Consultas auxiliares ─────────────────────────────────────────────────────


async def periodos(db: AsyncSession, obra_social: int):
    """Períodos entre los que elegir y el sugerido para el Colegio (último cerrado + 1)."""
    base = await nucleo.periodos_disponibles(db, obra_social)
    try:
        sugerido = await fact.get_periodo_activo(db, str(obra_social))
    except HTTPException:
        # Sin ningún período cerrado todavía: no hay piso, se puede elegir cualquiera.
        return PeriodosPlanillaOut(sugerido=base.sugerido, periodos=base.periodos, minimo=None)
    opciones = {p.periodo: p for p in base.periodos}
    if sugerido not in opciones:
        opciones[sugerido] = PeriodoOpcion(periodo=sugerido, cerrado=False, sugerido=True)
    return PeriodosPlanillaOut(
        minimo=sugerido,
        sugerido=sugerido,
        periodos=[
            PeriodoOpcion(periodo=p, cerrado=o.cerrado, sugerido=(p == sugerido))
            for p, o in sorted(opciones.items(), reverse=True)
        ],
    )


async def estado_lote(db: AsyncSession, *, obra_social: int, periodo: str, lote: str) -> LoteOut:
    """Cuántas prestaciones quedaron grabadas con ese lote. Sirve para saber, después
    de cancelar, si el commit llegó a ocurrir antes de la cancelación."""
    M = DetalleFacturacionCMC
    n, total = (await db.execute(
        select(func.count(), func.coalesce(func.sum(M.importe_total), 0)).where(
            M.cod_obr == str(obra_social),
            M.periodo == periodo,
            M.validacion_detalle.like(f"{PREFIJO_LOTE}{lote} ·%"),
        )
    )).one()
    return LoteOut(lote=lote, grabadas=int(n), importe_total=quantize_money(Decimal(total or 0)))
