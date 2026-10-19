"""Detalle de facturación en PDF — fpdf2.

Blanco y negro y sin grilla: las filas se escriben con `cell(..., border=0)`. La única tinta,
además del texto, son las líneas del encabezado de columnas y lo que separa un bloque del siguiente:
gris clarito en el total de cada socio y de cada paciente (de sanatorio, y de honorarios en «Por
tipo»), y una línea fina en el total de cada paciente de honorarios en «Por socio» y en el subtotal
de cada sección («Por tipo»).
Sin la grilla las filas son más bajas (menos hojas) y el documento se genera más rápido con miles
de filas.

Estructura (la que se eligió entre las propuestas de formato):
- Encabezado de 3 líneas en la primera hoja (el período y la obra social van al costado del
  nombre del Colegio y de la dirección) y una sola línea en las siguientes.
- Sin fila de título por socio ni subtítulos «CONSULTAS» / «PRACTICAS»: cada fila ya lleva el
  socio y la columna TIPO. Cada socio cierra con un renglón de totales por tipo.
- Honorarios individuales: subtítulo centrado y, debajo, «<clínica> - Paciente: <nombre>».
- Sanatorios: subtítulo centrado, debajo la clínica y debajo el paciente.
"""
from decimal import Decimal
from typing import Optional

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from app.modules.facturacion.export.armado import (
    ETIQUETA_TIPO,
    Armado,
    ColumnaSpec,
    TRAMOS_OCULTOS,
    con_columna_tipo,
    rotulo_paciente,
    spec_columnas,
    texto_resumen_socio,
    texto_subtotal_socio,
    texto_total_paciente,
    titulo_seccion,
    total_paciente_con_fondo,
    valores_fila,
)
from app.modules.facturacion.export.encabezado import EncabezadoExport
from app.modules.facturacion.export.schemas import ExportOpciones

_FONT = "Helvetica"
_SIZE_DATO = 6.5
# Un punto más que los datos: el encabezado de columnas se destaca.
_SIZE_HEADER_COL = 7.5
_SIZE_HEADER_COL_MIN = 5.5
_ALTO_FILA = 3.8
_ALTO_HEADER_COL = 4.2
# Gris clarito de las filas de total y aire entre un total y la primera fila del bloque siguiente.
_RELLENO_TOTAL = (235, 235, 235)
_AIRE_SEPARADOR = 1.6
# Margen superior del subtítulo de tramo («HONORARIOS INDIVIDUALES», en «Por socio»).
_AIRE_SUBTITULO = 1.6
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


def _par(pdf: FPDF, izq: str, der: str, alto: float, size: float, negrita_izq: bool, negrita_der: bool) -> None:
    """Una línea con texto a la izquierda y otro al costado derecho, al mismo nivel."""
    pdf.set_x(pdf.l_margin)
    pdf.set_font(_FONT, "B" if negrita_der else "", size)
    izq, der = str(izq or ""), str(der or "")
    w_der = pdf.get_string_width(der) + 2 * pdf.c_margin if der else 0
    pdf.set_font(_FONT, "B" if negrita_izq else "", size)
    pdf.cell(pdf.epw - w_der, alto, _ajustar_texto(pdf, izq, pdf.epw - w_der - 2 * pdf.c_margin),
             align="L", new_x=XPos.RIGHT, new_y=YPos.TOP)
    if der:
        pdf.set_font(_FONT, "B" if negrita_der else "", size)
        pdf.cell(w_der, alto, der, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        pdf.ln(alto)


def _escribir_encabezado(pdf: FPDF, enc: EncabezadoExport, completo: bool) -> None:
    """Primera hoja: 3 líneas (Colegio + período, domicilio + obra social, facturas). Siguientes:
    una sola línea. Sin los textos del costado derecho (encabezados armados a mano), cae al
    formato de 5 líneas."""
    lineas = enc.lineas
    pdf.set_xy(pdf.l_margin, pdf.t_margin)
    if not (enc.periodo or enc.obra_social):
        pdf.set_font(_FONT, "B", 10)
        pdf.cell(0, 4.5, lineas[0], align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font(_FONT, "", 7.5)
        for linea in lineas[1:]:
            pdf.set_x(pdf.l_margin)
            pdf.cell(0, 4, linea, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        return
    if completo:
        _par(pdf, lineas[0], enc.periodo, 4.5, 10, True, True)
        _par(pdf, lineas[1] if len(lineas) > 1 else "", enc.obra_social, 4, 7.5, False, True)
        _par(pdf, lineas[2] if len(lineas) > 2 else "", "", 4, 7.5, False, False)
    else:
        der = " - ".join(t for t in (enc.obra_social, enc.periodo) if t)
        _par(pdf, lineas[0], der, 4.5, 8, True, False)


class _Tabla:
    """Paginación manual. No hay grilla: solo las líneas del encabezado de columnas y lo que cierra
    un bloque: fondo gris clarito (`cierre`) o una línea fina (`separador`)."""

    def __init__(self, pdf: FPDF, cols: list[ColumnaSpec], encabezado: EncabezadoExport):
        self.pdf = pdf
        self.cols = cols
        self.encabezado = encabezado

        # Las columnas siempre ocupan todo el ancho útil de la hoja: si hay pocas se ensanchan en
        # proporción a su ancho base, si hay muchas se achican (antes solo se achicaban y, al quitar
        # columnas, la tabla quedaba angosta pegada a la izquierda).
        total = sum(c.ancho_mm for c in cols) or 1
        factor = pdf.epw / total
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

        # Los totales van alineados a la derecha, terminando donde terminan los importes (SUB. TOTAL).
        self.x_total = next((x + w for c, x, w in zip(cols, self.xs, self.anchos) if c.key == "sub_total"), self.x1)

        self._abierta = False
        self._titulo_seccion: Optional[str] = None

    def _abrir_pagina(self, titulo_seccion: Optional[str]) -> None:
        pdf = self.pdf
        pdf.add_page()
        _escribir_encabezado(pdf, self.encabezado, completo=pdf.page_no() == 1)
        if titulo_seccion:
            pdf.ln(0.5)
            pdf.set_x(self.x0)
            pdf.set_font(_FONT, "B", 9)
            pdf.cell(self.x1 - self.x0, 5, titulo_seccion, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1)

        y = pdf.get_y()
        for c, x, w, w_texto in zip(self.cols, self.xs, self.anchos, self._anchos_texto):
            pdf.set_xy(x, y)
            # El título se achica hasta entrar entero (AUTORIZACION, Nro. AFILIADO) en vez de cortarse con "...".
            size = _SIZE_HEADER_COL
            pdf.set_font(_FONT, "B", size)
            while size > _SIZE_HEADER_COL_MIN and pdf.get_string_width(c.header) > w_texto:
                size -= 0.25
                pdf.set_font(_FONT, "B", size)
            texto = _ajustar_texto(pdf, c.header, w_texto)
            pdf.cell(w, _ALTO_HEADER_COL, texto, align="C", new_x=XPos.RIGHT, new_y=YPos.TOP)
        y_fin = y + _ALTO_HEADER_COL
        pdf.line(self.x0, y, self.x1, y)
        pdf.line(self.x0, y_fin, self.x1, y_fin)
        pdf.set_xy(self.x0, y_fin)
        self._abierta = True

    def _espacio_libre(self) -> float:
        return self.pdf.h - self.pdf.b_margin - self.pdf.get_y()

    def asegurar_pagina(self, titulo_seccion: Optional[str] = None, forzar: bool = False) -> None:
        if forzar:
            self._titulo_seccion = titulo_seccion
        if forzar or not self._abierta or self._espacio_libre() < _ALTO_FILA:
            # Cada página lleva el título de su sección (CONSULTA, PRACTICA…), no sólo la primera.
            self._abrir_pagina(titulo_seccion or self._titulo_seccion)

    def espacio(self, mm: float) -> None:
        """Un poco de aire antes de la fila siguiente (no al principio de una hoja)."""
        self.asegurar_pagina()
        self.pdf.set_y(self.pdf.get_y() + mm)

    def fila(self, valores: list) -> None:
        self.asegurar_pagina()
        pdf = self.pdf
        pdf.set_font(_FONT, "", _SIZE_DATO)
        y = pdf.get_y()
        for c, x, w, w_texto, v in zip(self.cols, self.xs, self.anchos, self._anchos_texto, valores):
            pdf.set_xy(x, y)
            texto = _ajustar_texto(pdf, _fmt(v), w_texto)
            pdf.cell(w, _ALTO_FILA, texto, align=_MAP_ALIGN.get(c.alineacion, "L"),
                     new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.set_xy(self.x0, y + _ALTO_FILA)

    def fila_texto_libre(
        self, texto: str, negrita: bool = True, centrado: bool = False, cursiva: bool = False,
        cierre: bool = False, separador: bool = False, derecha: bool = False,
    ) -> None:
        """Una fila combinada de punta a punta. `cierre` es una fila de total con fondo gris clarito de
        punta a punta; `separador`, un total con una línea fina debajo. En los dos casos queda un
        pequeño margen antes de la fila siguiente. `derecha`: el texto termina en el borde derecho
        de SUB. TOTAL."""
        self.asegurar_pagina()
        pdf = self.pdf
        estilo = ("B" if negrita else "") + ("I" if cursiva else "")
        pdf.set_font(_FONT, estilo, _SIZE_DATO)
        ancho = (self.x_total if derecha else self.x1) - self.x0
        align = "R" if derecha else ("C" if centrado else "L")
        y = pdf.get_y()
        if cierre:
            alto = len(pdf.multi_cell(ancho, _ALTO_FILA, texto, align=align, dry_run=True, output="LINES")) * _ALTO_FILA
            pdf.set_fill_color(*_RELLENO_TOTAL)
            pdf.rect(self.x0, y, self.x1 - self.x0, alto, style="F")
        pdf.set_xy(self.x0, y)
        pdf.multi_cell(ancho, _ALTO_FILA, texto, align=align, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if separador:
            y_linea = pdf.get_y()
            pdf.line(self.x0, y_linea, self.x1, y_linea)
        if cierre or separador:
            pdf.set_xy(self.x0, pdf.get_y() + _AIRE_SEPARADOR)


def _pagina_resumen_general(pdf: FPDF, armado: Armado, enc: EncabezadoExport) -> None:
    pdf.add_page()
    _escribir_encabezado(pdf, enc, completo=True)
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
    pdf.cell(w2, 8, _fmt(armado.resumen.total_general), border=1, align="R",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    if armado.resumen.mostrar_coseguro:
        pdf.ln(5)
        pdf.set_font(_FONT, "B", 10)
        pdf.cell(0, 8, "RESUMEN DE COSEGUROS", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font(_FONT, "B", 10)
        pdf.set_xy(x0, pdf.get_y())
        pdf.cell(w1, 8, "TOTAL GENERAL COSEGUROS", border=1, align="R")
        pdf.cell(w2, 8, _fmt(armado.resumen.total_coseguro), border=1, align="R",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def build_pdf_detalle(
    armado: Armado, opciones: ExportOpciones, encabezado: EncabezadoExport,
) -> bytes:
    cols = spec_columnas(opciones.columnas, con_tipo=con_columna_tipo(opciones))
    pdf = _FPDFConPie(orientation="L", unit="mm", format="A4")
    pdf.alias_nb_pages()
    # margen inferior de 10mm: deja lugar al pie "Página X de Y" sin que una
    # fila de datos se dibuje encima.
    pdf.set_auto_page_break(False, margin=10)
    pdf.set_margins(6, 8, 6)
    pdf.set_compression(True)

    tabla = _Tabla(pdf, cols, encabezado)
    for seccion in armado.secciones:
        tabla.asegurar_pagina(titulo_seccion=titulo_seccion(seccion.titulo), forzar=True)
        hay_resumen_grupo = False
        for grupo in seccion.grupos:
            for linea in grupo.lineas:
                if linea.subtitulo and linea.subtitulo not in TRAMOS_OCULTOS:
                    tabla.espacio(_AIRE_SUBTITULO)
                    tabla.fila_texto_libre(linea.subtitulo, negrita=False, cursiva=True, centrado=True)
                if linea.subtitulo_clinica:
                    tabla.fila_texto_libre(linea.subtitulo_clinica, centrado=True)
                if linea.subtitulo_paciente:
                    tabla.fila_texto_libre(rotulo_paciente(linea), centrado=True)
                tabla.fila(valores_fila(cols, linea.fila))
                for hijo in linea.hijos:
                    tabla.fila(valores_fila(cols, hijo, es_hijo=True))
                if linea.total_paciente:
                    con_gris = total_paciente_con_fondo(linea, opciones)
                    tabla.fila_texto_libre(
                        texto_total_paciente(linea), cierre=con_gris, separador=not con_gris, derecha=True,
                    )
            if grupo.subtotal_medico:
                tabla.fila_texto_libre(texto_subtotal_socio(grupo), cierre=True, derecha=True)
            if grupo.mostrar_resumen:
                tabla.fila_texto_libre(texto_resumen_socio(grupo), cierre=True, derecha=True)
                hay_resumen_grupo = True
        if len(armado.secciones) > 1 and not hay_resumen_grupo:
            etiqueta = f"SUBTOTAL {titulo_seccion(seccion.titulo)}" if seccion.titulo else "SUBTOTAL"
            tabla.fila_texto_libre(f"{etiqueta}: $ {seccion.total:,.2f}", separador=True, derecha=True)

    _pagina_resumen_general(pdf, armado, encabezado)

    return bytes(pdf.output())
