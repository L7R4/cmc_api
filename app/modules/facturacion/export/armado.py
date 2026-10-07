"""Orden, agrupación, cortes de control y acumuladores del detalle.

Es la única pieza que conoce las reglas de negocio del armado — `excel.py` y
`pdf.py` sólo recorren la estructura que arma este módulo y la dibujan cada
uno en su formato. Así los dos formatos quedan matemáticamente idénticos.

## Misma estructura y orden que la vista del listado

El documento replica lo que muestra `FacturaDetalle` (front): cada prestación va en el
socio que cobra (`cod_medico` es siempre el médico, nunca la clínica). Las prestaciones de
tipo Sanatorio llevan además, por cada clínica, un subtítulo con su nombre. El
equipo de una cabeza (ayudante/gastos/pediatra) va ÚNICAMENTE bajo ella y cuenta en su
grupo (ver "Equipo"); el resto va como un socio más.

Las modalidades (`ExportOpciones.agrupacion`) comparten una sola estructura:
`Armado.secciones: list[Seccion]`, cada `Seccion` con uno o más `GrupoSocio`.

- **por_socio**: orden FIJO (ignora `orden`), una única `Seccion` sin título. Socios
  A-Z; dentro de cada uno, con subtítulo por tramo: consultas y prácticas por fecha
  (más nueva primero), honorarios individuales y sanatorios por paciente A-Z, cada
  paciente con su subtítulo ("PACIENTE <nombre>") y su "TOTAL PACIENTE <nombre>".
  Cada grupo lleva su título y su "RESUMEN SOCIO"; en Excel va todo en una sola hoja
  (`Armado.una_hoja`).
- **por_tipo**: una `Seccion` por tipo (Consulta/Practica/Honorarios
  individuales/Sanatorio) que tenga filas, cada una arranca en página/hoja nueva con
  su subtotal. Dentro, un grupo por socio (A-Z) con sus filas ordenadas por `orden` +
  `direccion` y, al cerrarlo, el subtotal de ese socio antes de pasar al siguiente. En
  Honorarios individuales y Sanatorios ordenados por paciente, cada paciente lleva su
  subtítulo y cierra con "TOTAL PACIENTE <nombre>" (en lugar del subtotal del socio).
- **plana**: una única `Seccion` sin título, un único pseudo-grupo sin RESUMEN, filas
  ordenadas directamente por `orden` + `direccion`. Ideal para pivotear en Excel.
- **todo_junto** (histórico): una única `Seccion` sin título con un `GrupoSocio` por
  socio y su "RESUMEN SOCIO"; `orden` decide el orden de los socios y DENTRO de cada
  uno el orden es el legacy fijo (tipo C→P→H→S, después fecha).

En todos los casos el documento cierra con `Armado.resumen` (RESUMEN GENERAL +
TOTAL GENERAL FACTURACIÓN), igual que el legacy.

## Equipo quirúrgico

El ayudante/gastos-de-equipo es una fila propia (`grupo_equipo_id` apunta al
`id_detalle_prestaciones` de la cabeza). Se anida SIEMPRE bajo la cabeza
(`LineaPrestacion.hijos`) y su importe cuenta en el subtotal de la CABEZA: no figura además
en su propio socio, así no aparece dos veces. Filtros y orden se aplican a la cabeza: si ella
queda afuera de los filtros, el equipo entero también. Si la cabeza ya no existe en la
factura (anulada), la fila es una línea común de su socio. Los ayudantes/pediatras cargados
sin grupo se asignan a su cabeza por paciente + fecha (`equipo.py`, ver `datos.py`).

En Honorarios individuales y Sanatorios el equipo se muestra pero NO suma a los totales del
socio ni de la sección (`_suma_equipo`); el RESUMEN GENERAL sí lo incluye, para cerrar con
el total de la factura. (`ExportOpciones.agrupar_equipo`
se conserva por compatibilidad con la API, pero ya no se consulta.)
"""
import datetime
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Optional

from app.common.money import quantize_money
from app.modules.facturacion.export.datos import FilaExport
from app.modules.facturacion.export.schemas import ExportOpciones
from app.modules.facturacion.service import TIPO_PRESTADOR_PEDIATRA

ORDEN_TIPO_LEGACY = {"Consulta": 0, "Practica": 1, "Honorarios individuales": 2, "Sanatorio": 3}
ETIQUETA_TIPO = {
    "Consulta": "CONSULTA", "Practica": "PRACTICA",
    "Honorarios individuales": "HONORARIO", "Sanatorio": "SANATORIO",
}
# Código de una letra para la columna TIPO del detalle (no para los resúmenes,
# que siguen con la etiqueta larga de ETIQUETA_TIPO). Mismas iniciales que el
# FIELD(tipo,'C','P','H','S') del legacy. Las filas de equipo (ayudante/gastos)
# se muestran como "A".
LETRA_TIPO = {
    "Consulta": "C", "Practica": "P",
    "Honorarios individuales": "H", "Sanatorio": "S",
}
LETRA_EQUIPO = "A"
# "PE", no "P": 'P' ya es Práctica en LETRA_TIPO — quedarían indistinguibles en la
# misma columna. Identifica al pediatra dentro de un equipo (hijo con tpo_funcion='P').
LETRA_PEDIATRA = "PE"
# Orden fijo de exhibición del resumen — igual que `$totales_globales_tipo` del legacy.
TIPOS_EN_ORDEN = ["Consulta", "Practica", "Honorarios individuales", "Sanatorio"]

_FECHA_MIN = datetime.date.min
_FECHA_HORA_MIN = datetime.datetime.min


@dataclass
class LineaPrestacion:
    fila: FilaExport
    # Integrantes del equipo de esta fila: se dibujan bajo ella y suman en su grupo.
    hijos: list[FilaExport] = field(default_factory=list)
    # Subtítulo del tramo que arranca en esta línea ("CONSULTAS", ...) — sólo en por_socio.
    subtitulo: str | None = None
    # Sanatorios: nombre de la clínica cuando empieza una nueva ("CLINICA X").
    subtitulo_clinica: str | None = None
    # Honorarios individuales / Sanatorios ordenados por paciente: arranca un paciente
    # ("PACIENTE X"), y en la última línea de cada paciente, su total (con el equipo).
    subtitulo_paciente: str | None = None
    total_paciente: tuple[str, Decimal] | None = None


@dataclass
class StatsTipo:
    cantidad: int = 0
    monto: Decimal = Decimal("0")


@dataclass
class GrupoSocio:
    cod_medico: str | None
    nombre: str | None
    matricula: int | None
    lineas: list[LineaPrestacion]
    mostrar_resumen: bool
    stats_por_tipo: dict[str, StatsTipo]
    total_honorarios: Decimal
    total_gastos: Decimal
    total_coseguro: Decimal
    total_general: Decimal
    # Fila de título antes de las líneas del grupo (por_socio: "1076 - ACOSTA, ...").
    titulo: str | None = None
    # por_tipo: al cerrar las líneas del socio, una línea con su subtotal.
    subtotal_medico: bool = False


@dataclass
class Seccion:
    titulo: str | None
    grupos: list[GrupoSocio]

    @property
    def total(self) -> Decimal:
        return sum((g.total_general for g in self.grupos), Decimal("0"))


@dataclass
class ResumenGeneral:
    por_tipo: list[tuple[str, Decimal]]  # [(tipo, monto)], sólo monto > 0, orden fijo
    total_general: Decimal
    mostrar_coseguro: bool
    total_coseguro: Decimal


@dataclass
class Armado:
    secciones: list[Seccion]
    resumen: ResumenGeneral
    total_prestaciones: int
    # Excel: todas las secciones en una sola hoja (con su título como fila de corte)
    # en vez de una hoja por sección.
    una_hoja: bool = False


def _nombre_clave(nombre: str | None, cod: str | None) -> tuple:
    return ((nombre or "").casefold(), cod or "")


def _clave_fila(f: FilaExport, orden: str):
    if orden == "nro_socio":
        try:
            return (int(f.cod_medico), "")
        except (TypeError, ValueError):
            return (10**12, f.cod_medico)
    if orden == "fecha_practica":
        return (f.fecha_practica or _FECHA_MIN, f.id)
    if orden == "fecha_carga":
        # `created` es al segundo: el id desempata las cargas simultáneas.
        return (f.created or _FECHA_HORA_MIN, f.id)
    if orden == "codigo":
        return f.codigo or ""
    if orden in ("importe", "importe_desc"):
        return f.subtotal
    if orden == "nombre_afiliado":
        return ((f.afiliado or "").casefold(), f.id)
    if orden == "especialidad":
        return (f.especialidad_nombre or "", f.prestador_nombre or "")
    # nombre_socio (default) y cualquier otro caso: por nombre del prestador.
    return _nombre_clave(f.prestador_nombre, f.cod_medico)


def _ordenar(filas: list[FilaExport], orden: str, direccion: str) -> list[FilaExport]:
    """Ordena por `orden` en el sentido `direccion`. `importe_desc` es siempre de mayor a
    menor (valor histórico). `reverse` conserva el orden relativo de los empates."""
    descendente = direccion == "desc" or orden == "importe_desc"
    return sorted(filas, key=lambda f: _clave_fila(f, orden), reverse=descendente)


def _clave_grupo(g: GrupoSocio, orden: str):
    filas = [l.fila for l in g.lineas]
    if orden == "nro_socio":
        try:
            return (int(g.cod_medico), "")
        except (TypeError, ValueError):
            return (10**12, g.cod_medico or "")
    if orden == "fecha_practica":
        fechas = [f.fecha_practica for f in filas if f.fecha_practica]
        return min(fechas) if fechas else _FECHA_MIN
    if orden == "fecha_carga":
        cargas = [f.created for f in filas if f.created]
        return min(cargas) if cargas else _FECHA_HORA_MIN
    if orden == "codigo":
        codigos = [f.codigo for f in filas if f.codigo]
        return min(codigos) if codigos else ""
    if orden in ("importe", "importe_desc"):
        return g.total_general
    if orden == "especialidad":
        return (filas[0].especialidad_nombre or "") if filas else ""
    # nombre_socio y nombre_afiliado (no aplica a nivel grupo, cae al nombre del socio).
    return _nombre_clave(g.nombre, g.cod_medico)


def _clave_legacy_interna(f: FilaExport):
    """Orden fijo DENTRO de un socio en modo `todo_junto` — el mismo que usaba
    el PHP: `FIELD(tipo,'C','P','H','S') → FECHA ASC`, nunca elegible."""
    return (ORDEN_TIPO_LEGACY.get(f.tipo, 9), f.fecha_practica or datetime.date.max, f.id)


def _equipo_por_cabeza(
    filas: list[FilaExport], cabezas: set[int],
) -> dict[int, list[FilaExport]]:
    """Integrantes del equipo de cada cabeza presente en el documento (`cabezas`), SIN la
    cabeza. Se arma con todas las filas, también las que el filtro dejó afuera: el equipo
    va completo bajo su cabeza."""
    out: dict[int, list[FilaExport]] = {}
    for f in filas:
        if f.grupo_equipo_id is not None and f.grupo_equipo_id != f.id and f.grupo_equipo_id in cabezas:
            out.setdefault(f.grupo_equipo_id, []).append(f)
    for miembros in out.values():
        miembros.sort(key=lambda f: f.id)
    return out


def _nombre_clinica(f: FilaExport) -> str:
    return f.clinica_nombre or str(f.cod_clinica or "")


def _clave_clinica(f: FilaExport) -> str:
    return _nombre_clinica(f).casefold()


def _marcar_clinicas(lineas: list[LineaPrestacion]) -> None:
    """Sanatorios: pone el subtítulo "CLINICA X" en la primera línea de cada clínica
    (las líneas ya vienen con las de una misma clínica seguidas)."""
    previa: object = None
    for linea in lineas:
        f = linea.fila
        if f.tipo != "Sanatorio":
            previa = None
            continue
        if f.cod_clinica != previa:
            previa = f.cod_clinica
            if f.cod_clinica:
                linea.subtitulo_clinica = f"CLINICA {_nombre_clinica(f)}"


def _lineas(
    filas_ordenadas: list[FilaExport], equipo: dict[int, list[FilaExport]],
) -> list[LineaPrestacion]:
    return [LineaPrestacion(fila=f, hijos=equipo.get(f.id, [])) for f in filas_ordenadas]


def _suma_equipo(cabeza: FilaExport) -> bool:
    """El equipo de una cirugía de Honorarios individuales o Sanatorio se muestra bajo su
    cabeza pero NO suma al total del socio; en el resto sí."""
    return cabeza.tipo not in ("Honorarios individuales", "Sanatorio")


def _stats_de_lineas(lineas: list[LineaPrestacion], equipo_siempre: bool = False) -> tuple[
    dict[str, StatsTipo], Decimal, Decimal, Decimal, Decimal,
]:
    """Totales de las líneas: la fila y su equipo anidado (`hijos`), salvo el de las
    cirugías de Honorarios individuales / Sanatorios (`equipo_siempre` lo incluye igual)."""
    stats: dict[str, StatsTipo] = {t: StatsTipo() for t in TIPOS_EN_ORDEN}
    total_hon = total_gas = total_cos = total_gral = Decimal("0")
    for linea in lineas:
        hijos = linea.hijos if equipo_siempre or _suma_equipo(linea.fila) else []
        for f in [linea.fila, *hijos]:
            s = stats.setdefault(f.tipo, StatsTipo())
            s.cantidad += (f.cantidad or 0) * (f.sesion or 1)
            s.monto += f.subtotal
            total_hon += f.honorarios
            total_gas += f.gastos
            total_cos += f.coseguro
            total_gral += f.subtotal
    return stats, total_hon, total_gas, total_cos, total_gral


def _armar_grupo(
    cod_medico: str | None, nombre: str | None, matricula: int | None,
    lineas: list[LineaPrestacion], mostrar_resumen: bool,
    titulo: str | None = None, subtotal_medico: bool = False,
) -> GrupoSocio:
    stats, hon, gas, cos, gral = _stats_de_lineas(lineas)
    return GrupoSocio(
        cod_medico=cod_medico, nombre=nombre, matricula=matricula,
        lineas=lineas, mostrar_resumen=mostrar_resumen,
        stats_por_tipo=stats, total_honorarios=quantize_money(hon),
        total_gastos=quantize_money(gas), total_coseguro=quantize_money(cos),
        total_general=quantize_money(gral), titulo=titulo, subtotal_medico=subtotal_medico,
    )


def _por_socio(filas: list[FilaExport]) -> dict[str, list[FilaExport]]:
    out: dict[str, list[FilaExport]] = {}
    for f in filas:
        out.setdefault(f.cod_medico, []).append(f)
    return out


def _titulo_socio(f: FilaExport) -> str:
    return f"{f.cod_medico} - {f.prestador_nombre}" if f.prestador_nombre else f"Socio {f.cod_medico}"


def _agrupar_todo_junto(
    filas: list[FilaExport], equipo: dict[int, list[FilaExport]], orden: str, direccion: str,
) -> list[GrupoSocio]:
    grupos = []
    for filas_socio in _por_socio(filas).values():
        f0 = filas_socio[0]
        lineas = _lineas(sorted(filas_socio, key=_clave_legacy_interna), equipo)
        grupos.append(_armar_grupo(f0.cod_medico, f0.prestador_nombre, f0.matricula, lineas, True))
    grupos.sort(key=lambda g: _clave_grupo(g, orden), reverse=(direccion == "desc" or orden == "importe_desc"))
    return grupos


def _clave_fecha_desc(f: FilaExport) -> tuple:
    """Más nueva primero; el id desempata de forma estable."""
    return (-(f.fecha_practica or _FECHA_MIN).toordinal(), f.id)


def _clave_natural(s: str | None) -> tuple:
    """Orden "natural" de un texto con números (2 antes que 10)."""
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p.casefold()) for p in re.findall(r"\d+|\D+", s or ""))


def _clave_paciente(f: FilaExport) -> tuple:
    """Paciente A-Z (nombre, después documento); dentro del mismo paciente, más nueva primero."""
    return (_normal(f.afiliado), _clave_natural(_normal(f.nro_afiliado)), *_clave_fecha_desc(f))


def _normal(s: str | None) -> str:
    return " ".join((s or "").split()).casefold()


def _marcar_pacientes(lineas: list[LineaPrestacion], con_total: bool = False) -> None:
    """Honorarios individuales / Sanatorios ordenados por paciente: pone el subtítulo
    "PACIENTE X" en la primera línea de cada paciente (las líneas ya vienen con las de un
    mismo paciente seguidas) y, con `con_total`, su total en la última — equipo incluido.
    El paciente se identifica por nombre y documento/afiliado y, en Sanatorios, también
    por clínica: el mismo paciente en otra clínica es otro bloque."""
    previa: tuple | None = None
    bloque: list[LineaPrestacion] = []

    def cerrar() -> None:
        if con_total and bloque:
            total = sum((f.subtotal for l in bloque for f in (l.fila, *l.hijos)), Decimal("0"))
            f0 = bloque[0].fila
            bloque[-1].total_paciente = (f0.afiliado or f0.nro_afiliado or "", quantize_money(total))
        bloque.clear()

    for linea in lineas:
        f = linea.fila
        if f.tipo not in ("Honorarios individuales", "Sanatorio"):
            cerrar()
            previa = None
            continue
        # La misma clave que ordena (`_clave_paciente`: nombre y número), así un bloque
        # nunca se parte por tener las filas del paciente separadas.
        clave = (_normal(f.afiliado), _normal(f.nro_afiliado), f.cod_clinica if f.tipo == "Sanatorio" else None)
        if clave != previa:
            cerrar()
            previa = clave
            linea.subtitulo_paciente = f"PACIENTE {f.afiliado or f.nro_afiliado or ''}".strip()
        bloque.append(linea)
    cerrar()


# Tramos dentro de cada socio en por_socio: (tipo, subtítulo, orden del tramo).
_TRAMOS_MEDICO: list[tuple[str, str, Callable[[FilaExport], tuple]]] = [
    ("Consulta", "CONSULTAS", _clave_fecha_desc),
    ("Practica", "PRACTICAS", _clave_fecha_desc),
    ("Honorarios individuales", "HONORARIOS INDIVIDUALES", _clave_paciente),
    ("Sanatorio", "SANATORIOS", lambda f: (_clave_clinica(f), *_clave_paciente(f))),
]


def _armar_por_socio(filas: list[FilaExport], equipo: dict[int, list[FilaExport]]) -> list[Seccion]:
    grupos: list[GrupoSocio] = []
    tipos_tramo = {t for t, _, _ in _TRAMOS_MEDICO}
    for filas_socio in _por_socio(filas).values():
        lineas: list[LineaPrestacion] = []
        for tipo, subtitulo, clave in _TRAMOS_MEDICO:
            tramo = sorted((f for f in filas_socio if f.tipo == tipo), key=clave)
            for i, f in enumerate(tramo):
                lineas.append(LineaPrestacion(
                    fila=f, hijos=equipo.get(f.id, []), subtitulo=subtitulo if i == 0 else None,
                ))
        # Filas legacy sin tipo reconocible: al final del socio, sin perderlas.
        otras = sorted((f for f in filas_socio if f.tipo not in tipos_tramo), key=_clave_fecha_desc)
        for i, f in enumerate(otras):
            lineas.append(LineaPrestacion(
                fila=f, hijos=equipo.get(f.id, []), subtitulo="OTRAS" if i == 0 else None,
            ))
        _marcar_clinicas(lineas)
        _marcar_pacientes(lineas, con_total=True)
        f0 = filas_socio[0]
        grupos.append(_armar_grupo(
            f0.cod_medico, f0.prestador_nombre, f0.matricula, lineas, True, titulo=_titulo_socio(f0),
        ))
    grupos.sort(key=lambda g: _nombre_clave(g.nombre, g.cod_medico))
    return [Seccion(titulo=None, grupos=grupos)] if grupos else []


def _armar_por_tipo(
    filas: list[FilaExport], equipo: dict[int, list[FilaExport]], opciones: ExportOpciones,
) -> list[Seccion]:
    orden, direccion = opciones.orden, opciones.direccion
    por_paciente = {
        "Honorarios individuales": opciones.orden_honorarios == "paciente",
        "Sanatorio": opciones.orden_sanatorio == "paciente",
    }
    secciones = []
    for tipo in TIPOS_EN_ORDEN:
        filas_tipo = [f for f in filas if f.tipo == tipo]
        if not filas_tipo:
            continue
        # Con "paciente" (sólo Honorarios individuales y Sanatorios) la sección entera va por
        # paciente A-Z, sin partir por socio (el socio está en su columna); en Sanatorios,
        # dentro de cada clínica (A-Z). Cada paciente cierra con su total, sin subtotal de socio.
        if por_paciente.get(tipo):
            clave = (lambda f: (_clave_clinica(f), *_clave_paciente(f))) if tipo == "Sanatorio" else _clave_paciente
            lineas = _lineas(sorted(filas_tipo, key=clave), equipo)
            _marcar_clinicas(lineas)
            _marcar_pacientes(lineas, con_total=True)
            secciones.append(Seccion(titulo=ETIQUETA_TIPO[tipo], grupos=[_armar_grupo(None, None, None, lineas, False)]))
            continue
        grupos = []
        for filas_socio in _por_socio(filas_tipo).values():
            f0 = filas_socio[0]
            if tipo in por_paciente:
                # "Médico": rige el orden elegido, y lo que ese orden no distingue (con
                # "nombre del socio", todas las filas del socio empatan) queda por
                # paciente A-Z en vez de en el orden de carga. `sorted` es estable,
                # también con `reverse`.
                ordenadas = _ordenar(sorted(filas_socio, key=_clave_paciente), orden, direccion)
            else:
                ordenadas = _ordenar(filas_socio, orden, direccion)
            if tipo == "Sanatorio":
                ordenadas = sorted(ordenadas, key=_clave_clinica)  # estable: conserva el orden dentro de cada clínica
            lineas = _lineas(ordenadas, equipo)
            _marcar_clinicas(lineas)
            grupos.append(_armar_grupo(
                f0.cod_medico, f0.prestador_nombre, f0.matricula, lineas, False, subtotal_medico=True,
            ))
        grupos.sort(key=lambda g: _nombre_clave(g.nombre, g.cod_medico))
        secciones.append(Seccion(titulo=ETIQUETA_TIPO[tipo], grupos=grupos))
    return secciones


def armar(filas: list[FilaExport], opciones: ExportOpciones) -> Armado:
    # El equipo de cada cabeza va SIEMPRE anidado bajo ella (completo, también las integrantes
    # que el filtro dejó afuera) y nunca como línea propia de su socio. Los filtros se aplican
    # a la cabeza: si ella queda afuera, su equipo entero queda afuera.
    ids_cabezas = {f.id for f in filas if f.grupo_equipo_id == f.id}
    cabezas_afuera = {f.id for f in filas if f.grupo_equipo_id == f.id and f.fuera_de_filtro}
    candidatas = [
        f for f in filas
        if not f.fuera_de_filtro and not (f.grupo_equipo_id in cabezas_afuera and f.grupo_equipo_id != f.id)
    ]
    cabezas = ids_cabezas - cabezas_afuera
    equipo = _equipo_por_cabeza(filas, cabezas)
    anidados = {h.id for hijos in equipo.values() for h in hijos}
    propias = [f for f in candidatas if f.id not in anidados]
    orden, direccion = opciones.orden, opciones.direccion

    if opciones.agrupacion == "todo_junto":
        grupos = _agrupar_todo_junto(propias, equipo, orden, direccion)
        secciones = [Seccion(titulo=None, grupos=grupos)]

    elif opciones.agrupacion == "por_socio":
        secciones = _armar_por_socio(propias, equipo)

    elif opciones.agrupacion == "por_tipo":
        secciones = _armar_por_tipo(propias, equipo, opciones)

    else:  # "plana"
        lineas = _lineas(_ordenar(propias, orden, direccion), equipo)
        secciones = [Seccion(titulo=None, grupos=[_armar_grupo(None, None, None, lineas, False)])]

    # ── Resumen general: sobre todas las filas del documento, una vez cada una, sin
    # depender de cómo se partió el documento.
    stats_global, _, _, total_coseguro, total_general = _stats_de_lineas(_lineas(propias, equipo), equipo_siempre=True)
    por_tipo = [
        (tipo, quantize_money(stats_global[tipo].monto))
        for tipo in TIPOS_EN_ORDEN
        if stats_global[tipo].monto > 0
    ]
    mostrar_coseguro = any(f.coseguro > 0 for f in [*propias, *anidados_filas(equipo)])
    resumen = ResumenGeneral(
        por_tipo=por_tipo, total_general=quantize_money(total_general),
        mostrar_coseguro=mostrar_coseguro, total_coseguro=quantize_money(total_coseguro),
    )

    return Armado(
        secciones=secciones, resumen=resumen,
        total_prestaciones=len(propias) + len(anidados),
        una_hoja=opciones.agrupacion == "por_socio",
    )


def anidados_filas(equipo: dict[int, list[FilaExport]]) -> list[FilaExport]:
    return [h for hijos in equipo.values() for h in hijos]


def texto_subtotal_medico(grupo: GrupoSocio, moneda: str = "") -> str:
    """Línea de cierre de un socio en por_tipo (la misma para Excel y PDF)."""
    nombre = f" {grupo.nombre}" if grupo.nombre else ""
    return (
        f"SUBTOTAL SOCIO {grupo.cod_medico}{nombre} ({len(grupo.lineas)}): "
        f"{moneda}{grupo.total_general:,.2f}"
    )


def texto_total_paciente(linea: LineaPrestacion, moneda: str = "") -> str:
    """Línea de cierre de un paciente (por_tipo ordenado por paciente), igual en Excel y PDF."""
    nombre, total = linea.total_paciente or ("", Decimal("0"))
    return f"TOTAL PACIENTE {nombre}: {moneda}{total:,.2f}" if nombre else f"TOTAL PACIENTE: {moneda}{total:,.2f}"


# ── Especificación de columnas (compartida por excel.py y pdf.py) ───────────
# SOCIO, SUB. TOTAL y TIPO/ROL están siempre — son las que identifican la fila
# y cierran el resumen. El resto es lo que `ExportOpciones.columnas` habilita;
# el orden acá abajo es el orden final en el documento, calcado del legacy con
# las columnas nuevas (diagnóstico/vía/especialidad/validación) al final.


@dataclass
class ColumnaSpec:
    key: str
    header: str
    ancho_mm: float
    ancho_excel: int
    alineacion: str  # "L" | "C" | "R"
    es_numero: bool
    valor: Callable[[FilaExport], object]


def _fmt_fecha(d: Optional[datetime.date]) -> str:
    # "-" y no un guión largo: los caracteres fuera de Latin-1 rompen fpdf2 con
    # las fuentes core (Helvetica) — ver el fix en pdf.py/caratula.py/routes.py.
    return d.strftime("%d/%m/%Y") if d else "-"


_DEFINICIONES: dict[str, ColumnaSpec] = {
    # Sin cortar por cantidad de caracteres: Helvetica es proporcional y un
    # nombre en mayúsculas de 30 caracteres puede medir bastante más que la
    # columna. El recorte real (por ancho medido, con "...") lo hace pdf.py al
    # dibujar; acá va el valor completo — Excel lo aprovecha entero.
    "prestador": ColumnaSpec("prestador", "PRESTADOR", 40, 26, "L", False, lambda f: f.prestador_nombre or ""),
    "obra_social": ColumnaSpec("obra_social", "OBRA SOCIAL", 40, 26, "L", False, lambda f: f.obra_social_nombre or f.cod_obr or ""),
    "matricula": ColumnaSpec("matricula", "MATRI.", 12, 8, "C", False, lambda f: f.matricula or ""),
    "autorizacion": ColumnaSpec("autorizacion", "AUTORIZACION", 22, 14, "C", False, lambda f: f.autorizacion or ""),
    "fecha": ColumnaSpec("fecha", "FECHA", 16, 11, "C", False, lambda f: _fmt_fecha(f.fecha_practica)),
    "codigo": ColumnaSpec("codigo", "CODIGO", 15, 10, "C", False, lambda f: f.codigo or ""),
    "nro_afiliado": ColumnaSpec("nro_afiliado", "Nro. AFILIADO", 20, 14, "C", False, lambda f: f.nro_afiliado or ""),
    "afiliado": ColumnaSpec("afiliado", "AFILIADO", 40, 26, "L", False, lambda f: f.afiliado or ""),
    "cantidad": ColumnaSpec("cantidad", "CANT.", 12, 8, "C", False, lambda f: f"{f.cantidad}-{f.sesion}"),
    "porcentaje": ColumnaSpec("porcentaje", "%", 9, 6, "R", True, lambda f: f.porcentaje or 0),
    "honorarios": ColumnaSpec("honorarios", "HONORARIOS", 22, 14, "R", True, lambda f: f.honorarios),
    "gastos": ColumnaSpec("gastos", "GASTOS", 22, 14, "R", True, lambda f: f.gastos),
    "coseguro": ColumnaSpec("coseguro", "COSEGURO", 22, 14, "R", True, lambda f: f.coseguro),
    "diagnostico": ColumnaSpec("diagnostico", "DIAGNOSTICO", 42, 28, "L", False, lambda f: f.diagnostico or ""),
    "via": ColumnaSpec("via", "VIA", 9, 6, "C", False, lambda f: f.via or ""),  # "T" / "L"
    "especialidad": ColumnaSpec("especialidad", "ESPECIALIDAD", 30, 22, "L", False, lambda f: f.especialidad_nombre or ""),
    "estado_validacion": ColumnaSpec("estado_validacion", "VALIDACION", 20, 14, "C", False, lambda f: f.estado_validacion or ""),
}

# Nº de registro = id_detalle_prestaciones. Va primera, es la que pidió el
# usuario para poder ubicar la fila exacta en la tabla. No es un monto: se
# muestra tal cual (es_numero=False, sin formato de moneda).
_COL_ID = ColumnaSpec("id", "Nro. REG.", 16, 11, "C", False, lambda f: f.id)
_COL_SOCIO = ColumnaSpec("socio", "SOCIO", 12, 8, "C", False, lambda f: f.cod_medico)
_COL_SUBTOTAL = ColumnaSpec("sub_total", "SUB. TOTAL", 22, 14, "R", True, lambda f: f.subtotal)
# El valor real de esta columna lo resuelve `etiqueta_tipo_rol` (via
# `valores_fila`), no este lambda — acá queda por consistencia del spec.
_COL_TIPO = ColumnaSpec("tipo", "TIPO", 10, 7, "C", False, lambda f: LETRA_TIPO.get(f.tipo, f.tipo or ""))


def spec_columnas(columnas_habilitadas: list[str], con_tipo: bool = True) -> list[ColumnaSpec]:
    """`con_tipo=False` saca la columna TIPO: agrupado por tipo, cada hoja/sección ya es
    de un solo tipo y la columna repetiría lo mismo en todas las filas."""
    seleccion = [_DEFINICIONES[k] for k in _DEFINICIONES if k in columnas_habilitadas]
    return [_COL_ID, _COL_SOCIO, *seleccion, _COL_SUBTOTAL, *([_COL_TIPO] if con_tipo else [])]


def con_columna_tipo(opciones: ExportOpciones) -> bool:
    return opciones.agrupacion != "por_tipo"


def etiqueta_tipo_rol(fila: FilaExport, es_hijo: bool) -> str:
    """Texto de la columna TIPO — código de una sola letra. La cabeza es
    C/P/H/S según el tipo; las filas de equipo son "A" (ayudante/gastos) o "PE"
    (pediatra — distinguido por su `tipo_prestador`, ya que por tipo/monto es
    indistinguible de un ayudante). Se dejó de mostrar la etiqueta larga y el sufijo
    "- CIRUJANO" a pedido del usuario, para achicar la columna."""
    if es_hijo:
        return LETRA_PEDIATRA if fila.tipo_prestador == TIPO_PRESTADOR_PEDIATRA else LETRA_EQUIPO
    return LETRA_TIPO.get(fila.tipo, fila.tipo or "")


def valores_fila(cols: list[ColumnaSpec], fila: FilaExport, es_hijo: bool = False) -> list:
    """Valores en el mismo orden que `cols`, con el caso especial de TIPO/ROL
    resuelto acá para que PDF y Excel no dupliquen la regla."""
    out = []
    for c in cols:
        out.append(etiqueta_tipo_rol(fila, es_hijo) if c.key == "tipo" else c.valor(fila))
    return out
