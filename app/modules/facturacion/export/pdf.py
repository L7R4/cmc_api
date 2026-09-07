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
from app.modules.facturacion.export.schemas import ExportOpciones

_FONT = "Helvetica"
_SIZE_DATO = 7
_SIZE_HEADER_COL = 7
_ALTO_FILA = 5.0
_MAP_ALIGN = {"L": "L", "C": "C", "R": "R"}


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return f"{v:,.2f}"
    return str(v)


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
    """Maneja paginación manual + la grilla dibujada en lote por página."""

    def __init__(self, pdf: FPDF, cols: list[ColumnaSpec], titulo_doc: str, meta_linea: str):
        self.pdf = pdf
        self.cols = cols
        self.titulo_doc = titulo_doc
        self.meta_linea = meta_linea

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
        self._bordes_verticales = [self.x0, *xs[1:], self.x1]

        self._y_inicio_pagina: Optional[float] = None
        self._limites_fila: list[float] = []

    def _abrir_pagina(self, titulo_seccion: Optional[str]) -> None:
        pdf = self.pdf
        pdf.add_page()
        pdf.set_font(_FONT, "B", 11)
        pdf.set_xy(self.x0, pdf.t_margin)
        pdf.cell(self.x1 - self.x0, 6, self.titulo_doc, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font(_FONT, "", 8)
        pdf.cell(self.x1 - self.x0, 5, self.meta_linea, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if titulo_seccion:
            pdf.set_font(_FONT, "B", 9)
            pdf.cell(self.x1 - self.x0, 5, titulo_seccion, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1)

        pdf.set_font(_FONT, "B", _SIZE_HEADER_COL)
        y = pdf.get_y()
        for c, x, w in zip(self.cols, self.xs, self.anchos):
            pdf.set_xy(x, y)
            pdf.cell(w, _ALTO_FILA, c.header, align="C", new_x=XPos.RIGHT, new_y=YPos.TOP)
        y_fin_header = y + _ALTO_FILA
        pdf.set_xy(self.x0, y_fin_header)

        self._y_inicio_pagina = y
        self._limites_fila = [y, y_fin_header]

    def _cerrar_pagina(self) -> None:
        if self._y_inicio_pagina is None:
            return
        pdf = self.pdf
        y0, y1 = self._limites_fila[0], self._limites_fila[-1]
        for x in self._bordes_verticales:
            pdf.line(x, y0, x, y1)
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
        for c, x, w, v in zip(self.cols, self.xs, self.anchos, valores):
            pdf.set_xy(x, y)
            pdf.cell(w, _ALTO_FILA, _fmt(v), align=_MAP_ALIGN.get(c.alineacion, "L"),
                      new_x=XPos.RIGHT, new_y=YPos.TOP)
        y_fin = y + _ALTO_FILA
        pdf.set_xy(self.x0, y_fin)
        self._limites_fila.append(y_fin)

    def fila_texto_libre(self, texto: str) -> None:
        self.asegurar_pagina()
        pdf = self.pdf
        pdf.set_font(_FONT, "B", _SIZE_DATO)
        pdf.set_xy(self.x0, pdf.get_y())
        pdf.multi_cell(self.x1 - self.x0, _ALTO_FILA, texto, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        y_fin = pdf.get_y()
        pdf.set_xy(self.x0, y_fin)
        self._limites_fila.append(y_fin)

    def cerrar(self) -> None:
        self._cerrar_pagina()


def _pagina_resumen_general(pdf: FPDF, armado: Armado) -> None:
    pdf.add_page()
    pdf.set_font(_FONT, "B", 12)
    pdf.set_xy(pdf.l_margin, pdf.t_margin)
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
    armado: Armado, opciones: ExportOpciones, titulo_doc: str, meta_linea: str,
) -> bytes:
    cols = spec_columnas(opciones.columnas)
    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.set_margins(6, 8, 6)
    pdf.set_compression(True)

    tabla = _Tabla(pdf, cols, titulo_doc, meta_linea)
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

    _pagina_resumen_general(pdf, armado)

    return bytes(pdf.output())
