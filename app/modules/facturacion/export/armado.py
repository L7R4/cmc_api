"""Orden, agrupación, cortes de control y acumuladores del detalle.

Es la única pieza que conoce las reglas de negocio del armado — `excel.py` y
`pdf.py` sólo recorren la estructura que arma este módulo y la dibujan cada
uno en su formato. Así los dos formatos quedan matemáticamente idénticos.

## Cómo se combinan agrupación + orden

Las 4 modalidades (`ExportOpciones.agrupacion`) comparten una sola estructura:
`Armado.secciones: list[Seccion]`, cada `Seccion` con uno o más `GrupoSocio`.

- **todo_junto**: una única `Seccion` sin título, con un `GrupoSocio` real por
  cada prestador (con su "RESUMEN SOCIO"). `orden` decide en qué orden aparecen
  esos grupos; DENTRO de cada grupo el orden es siempre el legacy fijo
  (tipo C→P→H→S, después fecha) — igual que hacía el PHP, que jamás dejaba
  elegir el orden interno del socio.
- **por_socio**: una `Seccion` por prestador (con su propio "RESUMEN SOCIO"),
  cada una arranca en página/hoja nueva. `orden` decide el orden de las
  secciones.
- **por_tipo**: una `Seccion` por tipo (Consulta/Practica/Honorarios
  individuales/Sanatorio) que tenga filas, cada una arranca en página/hoja
  nueva con su propio subtotal. Documento entero, y adentro sí, `orden` ordena
  las filas directamente (no hay corte por socio dentro de la sección: mezclar
  "separar por tipo" con "cortar por socio" fragmentaría el subtotal del tipo
  en pedacitos que nadie pidió).
- **plana**: una única `Seccion` sin título, un único pseudo-grupo sin RESUMEN,
  filas ordenadas directamente por `orden`. Es la salida más simple: encabezado
  + filas, ideal para pivotear en Excel.

En todos los casos el documento cierra con `Armado.resumen` (RESUMEN GENERAL +
TOTAL GENERAL FACTURACIÓN), igual que el legacy.

## Equipo quirúrgico

El ayudante/gastos-de-equipo es una fila propia (`grupo_equipo_id` apunta al
`id_detalle_prestaciones` de la cabeza). Se anida SIEMPRE bajo la cabeza,
en todas las modalidades — es una decisión sobre cómo se lee una prestación
de equipo, no sobre cómo se agrupa el documento. Su importe se computa dentro
del `GrupoSocio`/subtotal de la CABEZA (el cirujano), nunca del suyo propio:
así se comportaba el legacy y es lo que el usuario pidió mantener.
"""
import datetime
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

_FECHA_MAX = datetime.date.max


@dataclass
class LineaPrestacion:
    fila: FilaExport
    hijos: list[FilaExport] = field(default_factory=list)


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


def _clave_fila(f: FilaExport, orden: str):
    if orden == "nro_socio":
        try:
            return (int(f.cod_medico), "")
        except (TypeError, ValueError):
            return (10**12, f.cod_medico)
    if orden == "fecha_practica":
        return f.fecha_practica or _FECHA_MAX
    if orden == "codigo":
        return f.codigo or ""
    if orden == "importe_desc":
        return -f.subtotal
    if orden == "nombre_afiliado":
        return (f.afiliado or "", f.id)
    if orden == "especialidad":
        return (f.especialidad_nombre or "", f.prestador_nombre or "")
    # nombre_socio (default) y cualquier otro caso: por nombre del prestador.
    return (f.prestador_nombre or "", f.cod_medico)


def _clave_grupo(g: GrupoSocio, orden: str):
    filas = [l.fila for l in g.lineas]
    if orden == "nro_socio":
        try:
            return (int(g.cod_medico), "")
        except (TypeError, ValueError):
            return (10**12, g.cod_medico or "")
    if orden == "fecha_practica":
        fechas = [f.fecha_practica for f in filas if f.fecha_practica]
        return min(fechas) if fechas else _FECHA_MAX
    if orden == "codigo":
        codigos = [f.codigo for f in filas if f.codigo]
        return min(codigos) if codigos else ""
    if orden == "importe_desc":
        return -g.total_general
    if orden == "especialidad":
        return (filas[0].especialidad_nombre or "") if filas else ""
    # nombre_socio y nombre_afiliado (no aplica a nivel grupo, cae al nombre del socio).
    return (g.nombre or "", g.cod_medico or "")


def _clave_legacy_interna(f: FilaExport):
    """Orden fijo DENTRO de un socio en modo `todo_junto` — el mismo que usaba
    el PHP: `FIELD(tipo,'C','P','H','S') → FECHA ASC`, nunca elegible."""
    return (ORDEN_TIPO_LEGACY.get(f.tipo, 9), f.fecha_practica or _FECHA_MAX, f.id)


def _separar_equipo(filas: list[FilaExport]) -> tuple[list[FilaExport], dict[int, list[FilaExport]]]:
    """Separa las filas "cabeza" (o sueltas) de las filas "hijas" de un equipo
    quirúrgico. Una cabeza cumple `grupo_equipo_id == id`; un hijo tiene
    `grupo_equipo_id` distinto del propio id."""
    principales: list[FilaExport] = []
    hijos_por_cabeza: dict[int, list[FilaExport]] = {}
    for f in filas:
        if f.grupo_equipo_id is not None and f.grupo_equipo_id != f.id:
            hijos_por_cabeza.setdefault(f.grupo_equipo_id, []).append(f)
        else:
            principales.append(f)
    return principales, hijos_por_cabeza


def _lineas_con_equipo(
    filas_ordenadas: list[FilaExport], hijos_por_cabeza: dict[int, list[FilaExport]],
) -> list[LineaPrestacion]:
    return [
        LineaPrestacion(fila=f, hijos=hijos_por_cabeza.get(f.id, []))
        for f in filas_ordenadas
    ]


def _stats_de_lineas(lineas: list[LineaPrestacion]) -> tuple[
    dict[str, StatsTipo], Decimal, Decimal, Decimal, Decimal,
]:
    stats: dict[str, StatsTipo] = {t: StatsTipo() for t in TIPOS_EN_ORDEN}
    total_hon = total_gas = total_cos = total_gral = Decimal("0")
    for linea in lineas:
        for f in [linea.fila, *linea.hijos]:
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
    filas: list[FilaExport], hijos_por_cabeza: dict[int, list[FilaExport]],
    mostrar_resumen: bool,
) -> GrupoSocio:
    lineas = _lineas_con_equipo(filas, hijos_por_cabeza)
    stats, hon, gas, cos, gral = _stats_de_lineas(lineas)
    return GrupoSocio(
        cod_medico=cod_medico, nombre=nombre, matricula=matricula,
        lineas=lineas, mostrar_resumen=mostrar_resumen,
        stats_por_tipo=stats, total_honorarios=quantize_money(hon),
        total_gastos=quantize_money(gas), total_coseguro=quantize_money(cos),
        total_general=quantize_money(gral),
    )


def _agrupar_por_socio(
    filas: list[FilaExport], hijos_por_cabeza: dict[int, list[FilaExport]], orden: str,
) -> list[GrupoSocio]:
    por_socio: dict[str, list[FilaExport]] = {}
    orden_aparicion: list[str] = []
    meta: dict[str, tuple[str | None, int | None]] = {}
    for f in filas:
        if f.cod_medico not in por_socio:
            por_socio[f.cod_medico] = []
            orden_aparicion.append(f.cod_medico)
            meta[f.cod_medico] = (f.prestador_nombre, f.matricula)
        por_socio[f.cod_medico].append(f)

    grupos = []
    for cod in orden_aparicion:
        nombre, matricula = meta[cod]
        filas_grupo = sorted(por_socio[cod], key=_clave_legacy_interna)
        grupos.append(_armar_grupo(cod, nombre, matricula, filas_grupo, hijos_por_cabeza, True))

    grupos.sort(key=lambda g: _clave_grupo(g, orden))
    return grupos


def armar(filas: list[FilaExport], opciones: ExportOpciones) -> Armado:
    total_prestaciones = len(filas)
    principales, hijos_por_cabeza = _separar_equipo(filas)

    if opciones.agrupacion == "todo_junto":
        grupos = _agrupar_por_socio(principales, hijos_por_cabeza, opciones.orden)
        secciones = [Seccion(titulo=None, grupos=grupos)]

    elif opciones.agrupacion == "por_socio":
        grupos = _agrupar_por_socio(principales, hijos_por_cabeza, opciones.orden)
        secciones = [
            Seccion(titulo=g.nombre or f"Socio {g.cod_medico}", grupos=[g])
            for g in grupos
        ]

    elif opciones.agrupacion == "por_tipo":
        secciones = []
        for tipo in TIPOS_EN_ORDEN:
            filas_tipo = sorted(
                (f for f in principales if f.tipo == tipo),
                key=lambda f: _clave_fila(f, opciones.orden),
            )
            if not filas_tipo:
                continue
            grupo = _armar_grupo(None, None, None, filas_tipo, hijos_por_cabeza, False)
            secciones.append(Seccion(titulo=ETIQUETA_TIPO[tipo], grupos=[grupo]))

    else:  # "plana"
        filas_ordenadas = sorted(principales, key=lambda f: _clave_fila(f, opciones.orden))
        grupo = _armar_grupo(None, None, None, filas_ordenadas, hijos_por_cabeza, False)
        secciones = [Seccion(titulo=None, grupos=[grupo])]

    # ── Resumen general: se computa sobre TODAS las filas (cabezas + hijos),
    # no sobre las secciones, para no depender de cómo se partió el documento.
    stats_global, _, _, total_coseguro, total_general = _stats_de_lineas(
        [LineaPrestacion(fila=f, hijos=[]) for f in filas]
    )
    por_tipo = [
        (tipo, quantize_money(stats_global[tipo].monto))
        for tipo in TIPOS_EN_ORDEN
        if stats_global[tipo].monto > 0
    ]
    mostrar_coseguro = any(f.coseguro > 0 for f in filas)
    resumen = ResumenGeneral(
        por_tipo=por_tipo, total_general=quantize_money(total_general),
        mostrar_coseguro=mostrar_coseguro, total_coseguro=quantize_money(total_coseguro),
    )

    return Armado(secciones=secciones, resumen=resumen, total_prestaciones=total_prestaciones)


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
    "via": ColumnaSpec("via", "VIA", 16, 11, "C", False, lambda f: {"L": "Laparoscopica", "T": "Tradicional"}.get(f.via, f.via or "")),
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


def spec_columnas(columnas_habilitadas: list[str]) -> list[ColumnaSpec]:
    seleccion = [_DEFINICIONES[k] for k in _DEFINICIONES if k in columnas_habilitadas]
    return [_COL_ID, _COL_SOCIO, *seleccion, _COL_SUBTOTAL, _COL_TIPO]


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
