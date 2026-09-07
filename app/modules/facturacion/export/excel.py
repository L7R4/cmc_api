"""Excel del detalle de facturación — openpyxl en modo `write_only`.

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
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

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

FORMATO_MONEDA = "#,##0.00"
_ALINEACION = {"L": "left", "C": "center", "R": "right"}
_FUENTE_HEADER = Font(bold=True)
_FUENTE_NEGRITA = Font(bold=True)


def _sanitizar_nombre_hoja(nombre: str, usados: set[str]) -> str:
    limpio = re.sub(r"[\[\]:*?/\\]", " ", nombre).strip() or "Hoja"
    limpio = limpio[:28]
    candidato, i = limpio, 2
    while candidato.lower() in usados:
        candidato = f"{limpio[:25]} ({i})"
        i += 1
    usados.add(candidato.lower())
    return candidato


def _celda(ws, valor, *, negrita: bool = False, numero: bool = False, alineacion: str = "L"):
    c = WriteOnlyCell(ws, value=valor)
    if negrita:
        c.font = _FUENTE_NEGRITA
    if numero:
        c.number_format = FORMATO_MONEDA
    c.alignment = Alignment(horizontal=_ALINEACION.get(alineacion, "left"))
    return c


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
    for i, c in enumerate(cols, start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(c.ancho_excel, 6)
    # Congela todo lo de arriba (membrete + fila de encabezado de columnas):
    # a diferencia del PDF, en el Excel el membrete va una sola vez arriba de
    # todo — pero conviene que quede visible al scrollear.
    ws.freeze_panes = f"A{fila_encabezado_col + 1}"
    ws.auto_filter.ref = f"A{fila_encabezado_col}:{get_column_letter(len(cols))}{fila_encabezado_col}"
    _aplicar_pagina_a4(ws)


def _fila_encabezado_institucional(ws, encabezado: list[str]) -> None:
    """El membrete va una única vez, en las primeras filas de la hoja — no es
    una fila "repetible" de impresión como la numeración de página del PDF."""
    ws.append([_celda(ws, encabezado[0], negrita=True)])
    for linea in encabezado[1:]:
        ws.append([_celda(ws, linea)])
    ws.append([])


def _fila_encabezado(ws, cols: list[ColumnaSpec]) -> None:
    ws.append([_celda(ws, c.header, negrita=True, alineacion="C") for c in cols])


def _fila_dato(ws, cols: list[ColumnaSpec], fila, *, es_hijo: bool = False) -> None:
    valores = valores_fila(cols, fila, es_hijo)
    ws.append([
        _celda(ws, v, numero=c.es_numero, alineacion=c.alineacion)
        for c, v in zip(cols, valores)
    ])


def _fila_resumen_socio(ws, grupo: GrupoSocio) -> None:
    partes = [
        f"{ETIQUETA_TIPO.get(t, t)}: {s.cantidad} - {s.monto:,.2f}"
        for t, s in grupo.stats_por_tipo.items() if s.cantidad
    ]
    texto = f"RESUMEN SOCIO {grupo.cod_medico}: " + " | ".join(partes)
    texto += (
        f" | TOTAL SOCIO: {grupo.total_general:,.2f}"
        f" | HONORARIOS SOCIO: {grupo.total_honorarios:,.2f}"
        f" | GASTOS SOCIO: {grupo.total_gastos:,.2f}"
    )
    if grupo.total_coseguro > 0:
        texto += f" | COSEGURO SOCIO: {grupo.total_coseguro:,.2f}"
    ws.append([_celda(ws, texto, negrita=True)])


def _fila_subtotal_seccion(ws, titulo: Optional[str], total) -> None:
    etiqueta = f"SUBTOTAL {titulo}" if titulo else "SUBTOTAL"
    ws.append([_celda(ws, etiqueta, negrita=True), _celda(ws, total, negrita=True, numero=True, alineacion="R")])


def _escribir_resumen_general(ws, armado: Armado) -> None:
    ws.append([])
    ws.append([_celda(ws, "RESUMEN GENERAL DE PRESTACIONES", negrita=True)])
    for tipo, monto in armado.resumen.por_tipo:
        ws.append([
            _celda(ws, f"TOTAL {ETIQUETA_TIPO.get(tipo, tipo)}"),
            _celda(ws, monto, numero=True, alineacion="R"),
        ])
    ws.append([
        _celda(ws, "TOTAL GENERAL FACTURACIÓN", negrita=True),
        _celda(ws, armado.resumen.total_general, negrita=True, numero=True, alineacion="R"),
    ])
    if armado.resumen.mostrar_coseguro:
        ws.append([])
        ws.append([_celda(ws, "RESUMEN DE COSEGUROS", negrita=True)])
        ws.append([
            _celda(ws, "TOTAL GENERAL COSEGUROS"),
            _celda(ws, armado.resumen.total_coseguro, numero=True, alineacion="R"),
        ])


def build_excel_detalle(armado: Armado, opciones: ExportOpciones, encabezado: EncabezadoExport) -> bytes:
    from io import BytesIO

    cols = spec_columnas(opciones.columnas)
    fila_encabezado_col = len(encabezado.lineas) + 2  # + fila en blanco + la propia fila
    wb = Workbook(write_only=True)
    usados: set[str] = set()
    multi_hoja = len(armado.secciones) > 1

    ultima_ws = None
    for seccion in armado.secciones:
        nombre = _sanitizar_nombre_hoja(seccion.titulo or "Detalle", usados) if multi_hoja else "Detalle"
        ws = wb.create_sheet(nombre)
        _configurar_hoja(ws, cols, fila_encabezado_col)
        _fila_encabezado_institucional(ws, encabezado.lineas)
        _fila_encabezado(ws, cols)

        hay_resumen_grupo = False
        for grupo in seccion.grupos:
            for linea in grupo.lineas:
                _fila_dato(ws, cols, linea.fila)
                for hijo in linea.hijos:
                    _fila_dato(ws, cols, hijo, es_hijo=True)
            if grupo.mostrar_resumen:
                _fila_resumen_socio(ws, grupo)
                hay_resumen_grupo = True

        if multi_hoja and not hay_resumen_grupo:
            _fila_subtotal_seccion(ws, seccion.titulo, seccion.total)

        ultima_ws = ws

    if multi_hoja:
        ws_resumen = wb.create_sheet(_sanitizar_nombre_hoja("Resumen general", usados))
        _aplicar_pagina_a4(ws_resumen)
        _fila_encabezado_institucional(ws_resumen, encabezado.lineas)
        _escribir_resumen_general(ws_resumen, armado)
    else:
        _escribir_resumen_general(ultima_ws, armado)

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.read()
