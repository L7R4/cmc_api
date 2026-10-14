"""Plano de OMINT (O.S. 243) de una factura — SOLO LECTURA.

`GET /api/facturacion/facturas/{id}/export/omint.xlsx` (Herramientas → Plano OMINT). Reemplaza al
`archivo_plano_omint_1.php` del legacy, con las mismas ocho columnas:

    APELLIDO Y NOMBRE | NRO. BENEFICIARIO | FECHA | CODIGO PRESTACION | DESCRIPCION |
    MONTO | ESPECIALIDAD | TOKEN VIDEO LLAMADA

- Una fila por prestación del período (`estado <> 'X'`, misma versión de la factura). Los
  ayudantes ya son filas propias del detalle (el legacy los agregaba como filas extra del
  cirujano), con su especialidad y su valor.
- MONTO = `importe_total`, que ya viene neto de coseguro (el legacy restaba el coseguro a las
  consultas y prácticas) y multiplicado por cantidad y sesión.
- ESPECIALIDAD = la de la prestación; si no la trae, la principal del médico (lo que usaba el legacy).
- DESCRIPCION sale del nomenclador y, si el código no está, de `codigo_descripcion` (legacy).
- NRO. BENEFICIARIO: las prestaciones cargadas en el sistema nuevo lo traen en `dni_p`. Las
  migradas del legacy tienen `dni_p` = 0: se lee de `guardar_atencion` por `ga_id`.
- TOKEN VIDEO LLAMADA = `autorizacion` de la prestación.
- Orden: nombre del médico, tipo de orden (C, P, H, S), fecha de la práctica.

Contra el legacy (`guardar_atencion`) coinciden en cantidad de filas y en total los períodos
2026-06, 2026-07 y 2026-08 (en agosto el ayudante es una fila más: 180 = 179 + 1).
"""
import datetime
import io
from dataclasses import dataclass
from decimal import Decimal

from fastapi import HTTPException
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FacturacionCMC

OBRA_SOCIAL = 243
NOMBRE_OBRA_SOCIAL = "OMINT"
TITULO = "COLEGIO MEDICO CORRIENTES"
ENCABEZADOS = (
    "APELLIDO Y NOMBRE", "NRO. BENEFICIARIO", "FECHA", "CODIGO PRESTACION",
    "DESCRIPCION", "MONTO", "ESPECIALIDAD", "TOKEN VIDEO LLAMADA",
)
ANCHOS = (34, 20, 12, 18, 42, 14, 32, 22)
COLUMNAS = "ABCDEFGH"

# Las cargas viejas traen `tipo_orden` (C, P, H, S); las nuevas, `tipo` con el nombre del tipo.
SQL_FILAS = """
    select d.id_detalle_prestaciones as id, d.nom_ape_p, coalesce(nullif(nullif(trim(d.dni_p), ''), '0'), nullif(nullif(trim(g.NRO_AFILIADO), ''), '0')) as beneficiario, d.autorizacion,
           d.fecha_practica, d.cod_nom, d.importe_total, d.version,
           coalesce(
               (select e.ESPECIALIDAD from especialidad e where e.ID_COLEGIO_ESPE = d.id_especialidad limit 1),
               (select e.ESPECIALIDAD from especialidad e where e.ID_COLEGIO_ESPE = m.NRO_ESPECIALIDAD limit 1)
           ) as especialidad
    from detalle_facturacion d
    left join listado_medico m on m.NRO_SOCIO = d.cod_med
    left join guardar_atencion g on g.ID = d.ga_id
    where d.cod_obr = :os and d.periodo = :periodo and d.estado <> 'X'
    order by m.NOMBRE,
             coalesce(
                 field(d.tipo_orden, 'C', 'P', 'H', 'S'),
                 field(d.tipo, 'Consulta', 'Practica', 'Honorarios individuales', 'Sanatorio'),
                 0),
             d.fecha_practica, d.id_detalle_prestaciones
"""

# Primero el nomenclador y, para los códigos que no están, el catálogo legacy.
SQL_DESCRIPCIONES = (
    "select codigo, descripcion from nm_nomenclador where codigo in :codigos",
    "select CODIGO, DESCRIPCION from codigo_descripcion where CODIGO in :codigos",
)

_ARIAL = "Arial"
_FINO = Side(style="thin")
_BORDE = Border(left=_FINO, right=_FINO, top=_FINO, bottom=_FINO)
_CENTRO = Alignment(horizontal="center", vertical="center")
_IZQUIERDA = Alignment(horizontal="left", vertical="center")
_FORMATO_FECHA = "dd/mm/yyyy"
_FORMATO_MONEDA = "#,##0.00"


@dataclass
class PlanoOmint:
    contenido: bytes
    nombre_archivo: str
    filas: int
    total: Decimal


def _texto(valor) -> str:
    return " ".join(str(valor or "").split())


def _token(valor) -> str:
    # `autorizacion` viene vacía o en 0 cuando no hay.
    t = _texto(valor)
    return "" if t == "0" else t


def _libro(factura: FacturacionCMC, filas: list, descripciones: dict[str, str]) -> tuple[bytes, Decimal]:
    anio, mes = factura.periodo[:4], int(factura.periodo[4:6])
    wb = Workbook()
    ws = wb.active
    ws.title = f"plano omint {mes:02d}-{anio}"
    ws.sheet_view.showGridLines = False
    for letra, ancho in zip(COLUMNAS, ANCHOS):
        ws.column_dimensions[letra].width = ancho

    ws.merge_cells("A1:H1")
    ws["A1"] = TITULO
    ws["A1"].font = Font(name=_ARIAL, size=14, bold=True, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", start_color="000000", end_color="000000")
    ws["A1"].alignment = _CENTRO

    ws.merge_cells("A2:H2")
    ws["A2"] = f"OBRA SOCIAL: {NOMBRE_OBRA_SOCIAL} - PERIODO: {mes} - {anio}"
    ws["A2"].font = Font(name=_ARIAL, size=12, bold=True)
    ws["A2"].alignment = _CENTRO

    azul = PatternFill("solid", start_color="0000FF", end_color="0000FF")
    for col, titulo in enumerate(ENCABEZADOS, start=1):
        c = ws.cell(row=3, column=col, value=titulo)
        c.font = Font(name=_ARIAL, size=11, bold=True, color="FFFFFF")
        c.fill = azul
        c.alignment = _CENTRO
        c.border = _BORDE

    fuente = Font(name=_ARIAL, size=11)
    # (alineación, formato) por columna: nombre y descripción a la izquierda, el resto centrado.
    estilo = (
        (_IZQUIERDA, "@"), (_CENTRO, "@"), (_CENTRO, _FORMATO_FECHA), (_CENTRO, "@"),
        (_IZQUIERDA, "@"), (_CENTRO, _FORMATO_MONEDA), (_CENTRO, "@"), (_CENTRO, "@"),
    )
    total = Decimal(0)
    for r in filas:
        fecha = r["fecha_practica"] if isinstance(r["fecha_practica"], datetime.date) else None
        valor = Decimal(r["importe_total"] or 0)
        total += valor
        codigo = _texto(r["cod_nom"])
        ws.append([
            _texto(r["nom_ape_p"]), _texto(r["beneficiario"]), fecha, codigo,
            descripciones.get(codigo, ""), float(valor), _texto(r["especialidad"]), _token(r["autorizacion"]),
        ])
        fila = ws.max_row
        for col, (alineacion, formato) in enumerate(estilo, start=1):
            c = ws.cell(row=fila, column=col)
            c.font, c.alignment, c.border, c.number_format = fuente, alineacion, _BORDE, formato

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), total


async def descripciones_de(db: AsyncSession, codigos: set[str]) -> dict[str, str]:
    """Descripción de cada código: la del nomenclador y, si falta, la del catálogo legacy."""
    resultado: dict[str, str] = {}
    pendientes = sorted(codigos)
    for sql in SQL_DESCRIPCIONES:
        if not pendientes:
            break
        stmt = text(sql).bindparams(bindparam("codigos", expanding=True))
        for codigo, descripcion in (await db.execute(stmt, {"codigos": pendientes})).all():
            if codigo not in resultado and descripcion:
                resultado[codigo] = _texto(descripcion)
        pendientes = [c for c in pendientes if c not in resultado]
    return resultado


async def generar(db: AsyncSession, factura: FacturacionCMC) -> PlanoOmint:
    """Arma el Excel de `factura` (período cerrado o abierto)."""
    if str(factura.cod_obr).strip() != str(OBRA_SOCIAL):
        raise HTTPException(422, f"El plano de OMINT es sólo de la obra social {OBRA_SOCIAL}.")
    periodo = factura.periodo
    filas = (await db.execute(text(SQL_FILAS), {"os": OBRA_SOCIAL, "periodo": periodo})).mappings().all()
    filas = [f for f in filas if f["version"] == factura.version]
    if not filas:
        raise HTTPException(404, "La factura no tiene prestaciones para exportar.")
    descripciones = await descripciones_de(db, {_texto(f["cod_nom"]) for f in filas})
    contenido, total = _libro(factura, filas, descripciones)
    return PlanoOmint(
        contenido=contenido,
        nombre_archivo=f"plano omint {periodo[4:6]}-{periodo[:4]}.xlsx",
        filas=len(filas),
        total=total,
    )
