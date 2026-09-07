"""Detalle de facturación en PDF — fpdf2.

Con 17-20 mil filas, dibujar el borde de cada celda (`cell(..., border=1)`) son
del orden de 300.000 operaciones de trazo y llevan el documento a minutos. Acá
el contenido se escribe con `cell(..., border=0)` (la librería resuelve la
alineación, sin el costo del borde) y la grilla se dibuja aparte, en lote: una
línea horizontal por FILA (no por celda) y las verticales una única vez por
página. Sin colores de relleno ni fuentes embebidas — se imprime en blanco y
negro.
"""
from decimal import Decimal
from typing import Optional

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from app.modules.facturacion.export.armado import (
    ETIQUETA_TIPO,
    Armado,
    ColumnaSpec,
    GrupoSocio,
    spec_columnas,
    valores_fila,
)
from app.modules.facturacion.export.encabezado import EncabezadoExport
from app.modules.facturacion.export.schemas import ExportOpciones

_FONT = "Helvetica"
_SIZE_DATO = 6.5
_SIZE_HEADER_COL = 6.5
_ALTO_FILA = 4.4
_MAP_ALIGN = {"L": "L", "C": "C", "R": "R"}
_ELLIPSIS = "..."  # no "…": fuera de Latin-1, rompe las fuentes core de fpdf2.


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return f"{v:,.2f}"
    return str(v)


def _ajustar_texto(pdf: FPDF, texto: str, ancho_disponible: float) -> str:
    """Recorta `texto` para que entre en `ancho_disponible` (mm), midiendo con
    la fuente actual — no por cantidad de caracteres. Helvetica es proporcional:
    contar caracteres no dice nada del ancho real (un nombre en mayúsculas de
    30 caracteres puede medir el doble que uno con "iiii"). Sin esto, cualquier
    columna angosta con contenido largo desborda encima de la de al lado."""
    if not texto:
        return ""
    if pdf.get_string_width(texto) <= ancho_disponible:
        return texto

    ancho_sufijo = pdf.get_string_width(_ELLIPSIS)
    disponible = ancho_disponible - ancho_sufijo
    if disponible <= 0:
        return _ELLIPSIS

    # Estimación inicial por ancho promedio del propio texto (evita arrancar
    # desde el largo completo y achicar de a un caracter, que con textos largos
    # sería lento a 20.000 filas): normalmente sólo hacen falta 1-3 ajustes finos.
    ancho_total = pdf.get_string_width(texto)
    promedio = ancho_total / len(texto)
    n = min(len(texto), max(int(disponible / promedio), 0)) if promedio > 0 else 0

    while n > 0 and pdf.get_string_width(texto[:n]) > disponible:
        n -= 1
    while n < len(texto) and pdf.get_string_width(texto[:n + 1]) <= disponible:
        n += 1

    return texto[:n] + _ELLIPSIS if n < len(texto) else texto


class _FPDFConPie(FPDF):
    """Numeración "Página X de Y" al pie — Y sólo se conoce cuando termina el
    documento entero, por eso `{nb}` es un alias que fpdf2 reemplaza recién en
    `output()` (ver `alias_nb_pages()` en los builders)."""

    def footer(self) -> None:
        self.set_y(-8)
        self.set_font(_FONT, "", 7)
        self.cell(0, 5, f"Página {self.page_no()} de {{nb}}", align="C")


def _escribir_encabezado(pdf: FPDF, encabezado: list[str]) -> None:
    pdf.set_xy(pdf.l_margin, pdf.t_margin)
    pdf.set_font(_FONT, "B", 10)
    pdf.cell(0, 4.5, encabezado[0], align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font(_FONT, "", 7.5)
    for linea in encabezado[1:]:
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 4, linea, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _texto_resumen_socio(grupo: GrupoSocio) -> str:
    partes = [
        f"{ETIQUETA_TIPO.get(t, t)}: Cant: {s.cantidad} - $ {s.monto:,.2f}"
        for t, s in grupo.stats_por_tipo.items() if s.cantidad
    ]
    texto = f"RESUMEN SOCIO {grupo.cod_medico}: " + " | ".join(partes)
    texto += (
        f" | TOTAL SOCIO: $ {grupo.total_general:,.2f}"
        f" | HONORARIOS SOCIO: $ {grupo.total_honorarios:,.2f}"
        f" | GASTOS SOCIO: $ {grupo.total_gastos:,.2f}"
    )
    if grupo.total_coseguro > 0:
        texto += f" | COSEGURO SOCIO: $ {grupo.total_coseguro:,.2f}"
    return texto


class _Tabla:
    """Maneja paginación manual + la grilla dibujada en lote por página.

    Las filas de resumen (`fila_texto_libre`, RESUMEN SOCIO / SUBTOTAL de
    sección) ocupan una única "celda" combinada de punta a punta: no llevan
    separadores internos, sólo el marco exterior de la tabla, igual que una
    fila con colspan en HTML. Por eso las verticales internas (entre columnas)
    no se dibujan de una sola vez para toda la página como las exteriores —
    se cortan por "tramos" (`_segmentos_columnas`), uno por cada corrida de
    filas normales entre resúmenes."""

    def __init__(self, pdf: FPDF, cols: list[ColumnaSpec], encabezado: list[str]):
        self.pdf = pdf
        self.cols = cols
        self.encabezado = encabezado

        total = sum(c.ancho_mm for c in cols) or 1
        factor = min(1.0, pdf.epw / total)
        self.anchos = [c.ancho_mm * factor for c in cols]
        self.x0 = pdf.l_margin
        xs, x = [], self.x0
        for w in self.anchos:
            xs.append(x)
            x += w
        self.xs = xs
        self.x1 = x
        # Ancho disponible real para texto dentro de cada celda: fpdf2 aplica
        # `c_margin` (~1mm) de relleno a cada lado de un `cell()`.
        self._anchos_texto = [max(w - 2 * pdf.c_margin, 1) for w in self.anchos]

        self._y_inicio_pagina: Optional[float] = None
        self._limites_fila: list[float] = []
        self._segmentos_columnas: list[tuple[float, float]] = []
        self._segmento_actual_inicio: Optional[float] = None

    def _abrir_pagina(self, titulo_seccion: Optional[str]) -> None:
        pdf = self.pdf
        pdf.add_page()
        _escribir_encabezado(pdf, self.encabezado)
        if titulo_seccion:
            pdf.ln(0.5)
            pdf.set_x(self.x0)
            pdf.set_font(_FONT, "B", 9)
            pdf.cell(self.x1 - self.x0, 5, titulo_seccion, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1)

        pdf.set_font(_FONT, "B", _SIZE_HEADER_COL)
        y = pdf.get_y()
        for c, x, w, w_texto in zip(self.cols, self.xs, self.anchos, self._anchos_texto):
            pdf.set_xy(x, y)
            texto = _ajustar_texto(pdf, c.header, w_texto)
            pdf.cell(w, _ALTO_FILA, texto, align="C", new_x=XPos.RIGHT, new_y=YPos.TOP)
        y_fin_header = y + _ALTO_FILA
        pdf.set_xy(self.x0, y_fin_header)

        self._y_inicio_pagina = y
        self._limites_fila = [y, y_fin_header]
        self._segmentos_columnas = []
        self._segmento_actual_inicio = y  # el header también lleva separadores

    def _cerrar_segmento_columnas(self, y_fin: float) -> None:
        if self._segmento_actual_inicio is not None:
            self._segmentos_columnas.append((self._segmento_actual_inicio, y_fin))
            self._segmento_actual_inicio = None

    def _cerrar_pagina(self) -> None:
        if self._y_inicio_pagina is None:
            return
        pdf = self.pdf
        y0, y1 = self._limites_fila[0], self._limites_fila[-1]
        self._cerrar_segmento_columnas(y1)

        # Verticales exteriores: todo el alto de la tabla, sin cortes — el
        # marco se mantiene aunque una fila de resumen esté combinada por dentro.
        pdf.line(self.x0, y0, self.x0, y1)
        pdf.line(self.x1, y0, self.x1, y1)
        # Verticales internas: sólo dentro de cada tramo de filas con columnas.
        for x in self.xs[1:]:
            for seg_y0, seg_y1 in self._segmentos_columnas:
                pdf.line(x, seg_y0, x, seg_y1)
        # Horizontales: en cada límite de fila, sin excepción (incluye el
        # marco de arriba/abajo de una fila de resumen combinada).
        for y in self._limites_fila:
            pdf.line(self.x0, y, self.x1, y)

        self._y_inicio_pagina = None

    def _espacio_libre(self) -> float:
        return self.pdf.h - self.pdf.b_margin - self.pdf.get_y()

    def asegurar_pagina(self, titulo_seccion: Optional[str] = None, forzar: bool = False) -> None:
        if forzar or self._y_inicio_pagina is None or self._espacio_libre() < _ALTO_FILA:
            self._cerrar_pagina()
            self._abrir_pagina(titulo_seccion)

    def fila(self, valores: list, negrita: bool = False) -> None:
        self.asegurar_pagina()
        pdf = self.pdf
        pdf.set_font(_FONT, "B" if negrita else "", _SIZE_DATO)
        y = pdf.get_y()
        if self._segmento_actual_inicio is None:
            self._segmento_actual_inicio = y
        for c, x, w, w_texto, v in zip(self.cols, self.xs, self.anchos, self._anchos_texto, valores):
            pdf.set_xy(x, y)
            texto = _ajustar_texto(pdf, _fmt(v), w_texto)
            pdf.cell(w, _ALTO_FILA, texto, align=_MAP_ALIGN.get(c.alineacion, "L"),
                      new_x=XPos.RIGHT, new_y=YPos.TOP)
        y_fin = y + _ALTO_FILA
        pdf.set_xy(self.x0, y_fin)
        self._limites_fila.append(y_fin)

    def fila_texto_libre(self, texto: str) -> None:
        self.asegurar_pagina()
        pdf = self.pdf
        # Cierra el tramo de columnas actual justo antes de esta fila combinada
        # — sin esto las verticales internas seguirían de largo por encima del
        # texto, como si la fila tuviera columnas que en realidad no tiene.
        self._cerrar_segmento_columnas(pdf.get_y())
        pdf.set_font(_FONT, "B", _SIZE_DATO)
        pdf.set_xy(self.x0, pdf.get_y())
        pdf.multi_cell(self.x1 - self.x0, _ALTO_FILA, texto, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        y_fin = pdf.get_y()
        pdf.set_xy(self.x0, y_fin)
        self._limites_fila.append(y_fin)

    def cerrar(self) -> None:
        self._cerrar_pagina()


def _pagina_resumen_general(pdf: FPDF, armado: Armado, encabezado: list[str]) -> None:
    pdf.add_page()
    _escribir_encabezado(pdf, encabezado)
    pdf.ln(2)
    pdf.set_font(_FONT, "B", 12)
    pdf.set_x(pdf.l_margin)
    pdf.cell(0, 8, "RESUMEN GENERAL DE PRESTACIONES", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    x0, w1, w2 = pdf.l_margin + 40, 100, 40

    pdf.set_font(_FONT, "B", 10)
    y = pdf.get_y()
    pdf.set_xy(x0, y)
    pdf.cell(w1, 8, "CONCEPTO", border=1, align="C")
    pdf.cell(w2, 8, "TOTAL", border=1, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font(_FONT, "", 10)
    for tipo, monto in armado.resumen.por_tipo:
        pdf.set_xy(x0, pdf.get_y())
        pdf.cell(w1, 8, f"TOTAL {ETIQUETA_TIPO.get(tipo, tipo)}", border=1, align="L")
        pdf.cell(w2, 8, _fmt(monto), border=1, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font(_FONT, "B", 10)
    pdf.set_xy(x0, pdf.get_y())
    pdf.cell(w1, 8, "TOTAL GENERAL FACTURACION", border=1, align="R")
    pdf.cell(w2, 8, _fmt(armado.resumen.total_general), border=1, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    if armado.resumen.mostrar_coseguro:
        pdf.ln(5)
        pdf.set_font(_FONT, "B", 10)
        pdf.cell(0, 8, "RESUMEN DE COSEGUROS", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font(_FONT, "", 10)
        pdf.set_xy(x0, pdf.get_y())
        pdf.cell(w1, 8, "TOTAL GENERAL COSEGUROS", border=1, align="R")
        pdf.cell(w2, 8, _fmt(armado.resumen.total_coseguro), border=1, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def build_pdf_detalle(
    armado: Armado, opciones: ExportOpciones, encabezado: EncabezadoExport,
) -> bytes:
    cols = spec_columnas(opciones.columnas)
    pdf = _FPDFConPie(orientation="L", unit="mm", format="A4")
    pdf.alias_nb_pages()
    # margen inferior de 10mm: deja lugar al pie "Página X de Y" sin que una
    # fila de datos se dibuje encima.
    pdf.set_auto_page_break(False, margin=10)
    pdf.set_margins(6, 8, 6)
    pdf.set_compression(True)

    tabla = _Tabla(pdf, cols, encabezado.lineas)
    for seccion in armado.secciones:
        tabla.asegurar_pagina(titulo_seccion=seccion.titulo, forzar=True)
        hay_resumen_grupo = False
        for grupo in seccion.grupos:
            for linea in grupo.lineas:
                tabla.fila(valores_fila(cols, linea.fila))
                for hijo in linea.hijos:
                    tabla.fila(valores_fila(cols, hijo, es_hijo=True))
            if grupo.mostrar_resumen:
                tabla.fila_texto_libre(_texto_resumen_socio(grupo))
                hay_resumen_grupo = True
        if len(armado.secciones) > 1 and not hay_resumen_grupo:
            etiqueta = f"SUBTOTAL {seccion.titulo}" if seccion.titulo else "SUBTOTAL"
            tabla.fila_texto_libre(f"{etiqueta}: $ {seccion.total:,.2f}")
    tabla.cerrar()

    _pagina_resumen_general(pdf, armado, encabezado.lineas)

    return bytes(pdf.output())
