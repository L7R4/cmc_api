"""Excel del detalle de facturación — openpyxl en modo `write_only`.

Misma estructura y textos que el PDF (`armado.py` los comparte): sin fila de título por socio ni
subtítulos «CONSULTAS / PRACTICAS», «<clínica> - Paciente: <nombre>» en honorarios individuales,
totales alineados a la derecha (terminan en la columna SUB. TOTAL) con fondo gris clarito en el socio y
en el paciente de sanatorio / «Por tipo», y con una línea fina en el resto. Sin colores.

En `write_only` no hay forma de volver atrás a leer/editar una celda ya escrita
(por eso el helper `_autosize` de `app/modules/exports/service.py` NO sirve
acá: itera `ws.columns`, que no existe en este modo). Los anchos se fijan de
entrada según `ColumnaSpec.ancho_excel`. Todo el documento sale en una sola
pasada hacia adelante, con acumuladores ya resueltos por `armado.py`.
"""
import re
from typing import Optional

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.modules.facturacion.export.armado import (
    ETIQUETA_TIPO,
    TRAMOS_OCULTOS,
    Armado,
    ColumnaSpec,
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

FORMATO_MONEDA = "#,##0.00"
ALTO_FILA_EXCEL = 18
_ALINEACION = {"L": "left", "C": "center", "R": "right"}
# Encabezado de columnas: un punto más grande que el resto (11) y en negrita.
_FUENTE_HEADER = Font(bold=True, size=12)
_FUENTE_NEGRITA = Font(bold=True)
_FUENTE_CURSIVA = Font(italic=True)
# Totales de socio y de paciente (sanatorio / «Por tipo»): gris claro.
_RELLENO_TOTAL = PatternFill(fill_type="solid", start_color="EBEBEB", end_color="EBEBEB")
# Totales con línea fina debajo (en vez de fondo).
_LINEA_ABAJO = Border(bottom=Side(style="thin", color="000000"))


def _sanitizar_nombre_hoja(nombre: str, usados: set[str]) -> str:
    limpio = re.sub(r"[\[\]:*?/\\]", " ", nombre).strip() or "Hoja"
    limpio = limpio[:28]
    candidato, i = limpio, 2
    while candidato.lower() in usados:
        candidato = f"{limpio[:25]} ({i})"
        i += 1
    usados.add(candidato.lower())
    return candidato


def _celda(ws, valor, *, negrita: bool = False, cursiva: bool = False, numero: bool = False, alineacion: str = "L"):
    c = WriteOnlyCell(ws, value=valor)
    if negrita:
        c.font = _FUENTE_NEGRITA
    elif cursiva:
        c.font = _FUENTE_CURSIVA
    if numero:
        c.number_format = FORMATO_MONEDA
    c.alignment = Alignment(horizontal=_ALINEACION.get(alineacion, "left"), vertical="center")
    return c


def _fila_centrada(ws, n_cols: int, texto: str, *, negrita: bool = False, cursiva: bool = False) -> None:
    """Subtítulo centrado en el ancho de la tabla: el texto va en la primera celda y el
    resto de la fila hereda "centrar en la selección" (en `write_only` no se pueden combinar
    celdas, pero esto se ve igual y no estorba al filtrar)."""
    celdas = []
    for i in range(max(n_cols, 1)):
        c = WriteOnlyCell(ws, value=texto if i == 0 else None)
        if negrita:
            c.font = _FUENTE_NEGRITA
        elif cursiva:
            c.font = _FUENTE_CURSIVA
        c.alignment = Alignment(horizontal="centerContinuous", vertical="center")
        celdas.append(c)
    ws.append(celdas)


def _aplicar_pagina_a4(ws) -> None:
    # Que al imprimirse entre en A4: ancho a una página, alto sin límite de
    # páginas (con miles de filas no tiene sentido "una sola hoja de alto").
    # openpyxl no expone una constante para esto (a diferencia de otras libs);
    # "9" es el código de tamaño de papel A4 del estándar OOXML (ECMA-376).
    ws.page_setup.paperSize = "9"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.oddFooter.center.text = "Página &P de &N"


def _configurar_hoja(ws, cols: list[ColumnaSpec], fila_encabezado_col: int) -> None:
    # Filas un poco más altas que las de Excel por defecto (15), con el texto centrado.
    ws.sheet_format.defaultRowHeight = ALTO_FILA_EXCEL
    ws.sheet_format.customHeight = True
    for i, c in enumerate(cols, start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(c.ancho_excel, 6)
    # Congela todo lo de arriba (membrete + fila de encabezado de columnas):
    # a diferencia del PDF, en el Excel el membrete va una sola vez arriba de
    # todo — pero conviene que quede visible al scrollear.
    ws.freeze_panes = f"A{fila_encabezado_col + 1}"
    ws.auto_filter.ref = f"A{fila_encabezado_col}:{get_column_letter(len(cols))}{fila_encabezado_col}"
    _aplicar_pagina_a4(ws)


def _membrete(enc: EncabezadoExport) -> list[tuple[str, str]]:
    """Líneas del membrete como (texto izquierdo, texto derecho). Con período y obra social cargados son
    3 líneas (el período y la obra social van al costado, a la altura del nombre del Colegio y de la
    dirección); si no, las líneas tal cual."""
    lineas = enc.lineas
    if enc.periodo or enc.obra_social:
        return [
            (lineas[0], enc.periodo),
            (lineas[1] if len(lineas) > 1 else "", enc.obra_social),
            (lineas[2] if len(lineas) > 2 else "", ""),
        ]
    return [(linea, "") for linea in lineas]


def _fila_encabezado_institucional(ws, enc: EncabezadoExport, n_cols: int) -> None:
    """El membrete va una única vez, en las primeras filas de la hoja — no es
    una fila "repetible" de impresión como la numeración de página del PDF."""
    for i, (izq, der) in enumerate(_membrete(enc)):
        celdas = [_celda(ws, izq, negrita=i == 0)]
        if der and n_cols > 1:
            celdas += [None] * (n_cols - 2)
            celdas.append(_celda(ws, der, negrita=i == 0, alineacion="R"))
        ws.append(celdas)
    ws.append([])


def _fila_encabezado(ws, cols: list[ColumnaSpec]) -> None:
    celdas = []
    for c in cols:
        celda = _celda(ws, c.header, alineacion="C")
        celda.font = _FUENTE_HEADER
        celdas.append(celda)
    ws.append(celdas)


def _fila_total(ws, n_cols: int, *valores, numero_en: int | None = None) -> None:
    """Fila del resumen general: en negrita. `valores` van en las primeras celdas; `numero_en` es la
    posición del monto (formato moneda, alineado a la derecha)."""
    celdas = []
    for i in range(max(n_cols, len(valores), 1)):
        v = valores[i] if i < len(valores) else None
        es_numero = numero_en is not None and i == numero_en
        celdas.append(_celda(ws, v, negrita=True, numero=es_numero, alineacion="R" if es_numero else "L"))
    ws.append(celdas)


def _fila_cierre(ws, cols: list[ColumnaSpec], texto: str, *, con_fondo: bool, monto=None) -> None:
    """Fila de total de un bloque (paciente, socio, sección), en negrita y alineada a la derecha: el texto
    termina en la columna SUB. TOTAL (el texto alineado a la derecha se desborda hacia la izquierda sobre
    las celdas vacías). Con `monto`, el texto va en la celda anterior y el importe numérico en SUB. TOTAL.
    `con_fondo`: gris clarito en todo el ancho; si no, una línea fina debajo."""
    i_total = next((i for i, c in enumerate(cols) if c.key == "sub_total"), len(cols) - 1)
    i_texto = i_total - 1 if monto is not None and i_total > 0 else i_total
    celdas = []
    for i in range(len(cols)):
        valor = texto if i == i_texto else (monto if monto is not None and i == i_total else None)
        celda = _celda(ws, valor, negrita=True, numero=monto is not None and i == i_total, alineacion="R")
        if con_fondo:
            celda.fill = _RELLENO_TOTAL
        else:
            celda.border = _LINEA_ABAJO
        celdas.append(celda)
    ws.append(celdas)


def _fila_dato(ws, cols: list[ColumnaSpec], fila, *, es_hijo: bool = False) -> None:
    valores = valores_fila(cols, fila, es_hijo)
    ws.append([
        _celda(ws, v, numero=c.es_numero, alineacion=c.alineacion)
        for c, v in zip(cols, valores)
    ])


def _escribir_resumen_general(ws, armado: Armado) -> None:
    ws.append([])
    ws.append([_celda(ws, "RESUMEN GENERAL DE PRESTACIONES", negrita=True)])
    for tipo, monto in armado.resumen.por_tipo:
        ws.append([
            _celda(ws, f"TOTAL {ETIQUETA_TIPO.get(tipo, tipo)}"),
            _celda(ws, monto, numero=True, alineacion="R"),
        ])
    _fila_total(ws, 2, "TOTAL GENERAL FACTURACIÓN", armado.resumen.total_general, numero_en=1)
    if armado.resumen.mostrar_coseguro:
        ws.append([])
        ws.append([_celda(ws, "RESUMEN DE COSEGUROS", negrita=True)])
        _fila_total(ws, 2, "TOTAL GENERAL COSEGUROS", armado.resumen.total_coseguro, numero_en=1)


def build_excel_detalle(armado: Armado, opciones: ExportOpciones, encabezado: EncabezadoExport) -> bytes:
    from io import BytesIO

    cols = spec_columnas(opciones.columnas, con_tipo=con_columna_tipo(opciones))
    fila_encabezado_col = len(_membrete(encabezado)) + 2  # + fila en blanco + la propia fila
    wb = Workbook(write_only=True)
    usados: set[str] = set()
    multi_hoja = len(armado.secciones) > 1 and not armado.una_hoja

    ultima_ws = None
    for seccion in armado.secciones:
        if multi_hoja or ultima_ws is None:
            nombre = _sanitizar_nombre_hoja(titulo_seccion(seccion.titulo) or "Detalle", usados) if multi_hoja else "Detalle"
            ws = wb.create_sheet(nombre)
            _configurar_hoja(ws, cols, fila_encabezado_col)
            _fila_encabezado_institucional(ws, encabezado, len(cols))
            _fila_encabezado(ws, cols)
        if seccion.titulo:
            # En una sola hoja, el título de la sección es la fila de corte; con una hoja por
            # sección, es el título de la hoja.
            _fila_centrada(ws, len(cols), titulo_seccion(seccion.titulo), negrita=True)

        hay_resumen_grupo = False
        for grupo in seccion.grupos:
            for linea in grupo.lineas:
                if linea.subtitulo and linea.subtitulo not in TRAMOS_OCULTOS:
                    _fila_centrada(ws, len(cols), linea.subtitulo, cursiva=True)
                if linea.subtitulo_clinica:
                    _fila_centrada(ws, len(cols), linea.subtitulo_clinica, negrita=True)
                if linea.subtitulo_paciente:
                    _fila_centrada(ws, len(cols), rotulo_paciente(linea), negrita=True)
                _fila_dato(ws, cols, linea.fila)
                for hijo in linea.hijos:
                    _fila_dato(ws, cols, hijo, es_hijo=True)
                if linea.total_paciente:
                    _fila_cierre(ws, cols, texto_total_paciente(linea),
                                 con_fondo=total_paciente_con_fondo(linea, opciones))
            if grupo.subtotal_medico:
                _fila_cierre(ws, cols, texto_subtotal_socio(grupo), con_fondo=True)
            if grupo.mostrar_resumen:
                _fila_cierre(ws, cols, texto_resumen_socio(grupo), con_fondo=True)
                hay_resumen_grupo = True

        if (multi_hoja or (armado.una_hoja and len(armado.secciones) > 1)) and not hay_resumen_grupo:
            etiqueta = f"SUBTOTAL {titulo_seccion(seccion.titulo)}" if seccion.titulo else "SUBTOTAL"
            _fila_cierre(ws, cols, etiqueta, con_fondo=False, monto=seccion.total)

        ultima_ws = ws

    if multi_hoja:
        ws_resumen = wb.create_sheet(_sanitizar_nombre_hoja("Resumen general", usados))
        _aplicar_pagina_a4(ws_resumen)
        _fila_encabezado_institucional(ws_resumen, encabezado, len(cols))
        _escribir_resumen_general(ws_resumen, armado)
    else:
        _escribir_resumen_general(ultima_ws, armado)

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.read()
