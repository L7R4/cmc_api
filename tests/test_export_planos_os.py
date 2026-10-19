"""Planos de OMINT (Excel) y de DASUTEN (TXT): formato de cada fila, sin tocar la base."""
import datetime
import io
from decimal import Decimal
from types import SimpleNamespace

from openpyxl import load_workbook

from app.modules.facturacion.export import dasuten_txt, omint_xlsx


def _fila_dasuten(**kw) -> dict:
    base = {
        "id": 1, "beneficiario": "20093168", "cod_nom": "420351", "fecha_practica": datetime.date(2026, 9, 18),
        "cantidad": 1, "sesion": 1, "coseguro": Decimal("0"), "importe_total": Decimal("24802.82"),
        "honorarios": Decimal("24802.82"), "gastos": Decimal("0"), "ayudante": Decimal("0"), "tpo_funcion": "H",
        "prestador": "Zaracho Laura Viviana",
    }
    base.update(kw)
    return base


def test_dasuten_linea_mide_129_y_respeta_las_columnas():
    s = dasuten_txt.linea(_fila_dasuten(), "CONSULTA ESPECIALIZADA")
    assert len(s) == dasuten_txt.ANCHO == 129
    assert s[0:2] == "DU"
    assert s[2:12] == "20093168  "
    assert s[12:15] == "002"
    assert s[15:24] == "420351   "
    assert s[24:34] == "18/09/2026"
    assert s[34:64] == "CONSULTA ESPECIALIZADA".ljust(30)
    assert s[64:67] == "1  "
    assert s[67:79] == "248028200000"  # $ 24.802,82 en centavos, con ceros a la derecha como el legacy
    assert s[79:109] == "ZARACHO LAURA VIVIANA".ljust(30)
    assert s[109:111] == "0 "
    assert s[111:116] == "0    "
    assert s[116:119] == "1  "
    assert s[119:129] == "0000000000"


def test_dasuten_monto_es_honorarios_mas_gastos_por_cantidad_y_sesion_y_no_descuenta_coseguro():
    fila = _fila_dasuten(
        cantidad=2, sesion=3, honorarios=Decimal("1000"), gastos=Decimal("500"),
        coseguro=Decimal("4000"), importe_total=Decimal("1"),
    )
    s = dasuten_txt.linea(fila, "CONSULTA")
    assert s[64:67] == "2  "
    assert s[67:79] == "900000000000"  # (1.000 + 500) x 2 x 3 = 9.000,00
    assert s[119:129] == "4000000000"  # el coseguro, tal cual: 40,00 por unidad sin multiplicar


def test_dasuten_en_filas_de_ayudante_el_monto_sale_de_ayudante_y_no_de_honorarios():
    fila = _fila_dasuten(tpo_funcion="A", honorarios=Decimal("0"), ayudante=Decimal("250"), gastos=Decimal("50"))
    assert dasuten_txt.linea(fila, "X")[67:79] == "300000000000"


def test_dasuten_cantidad_y_sesion_en_cero_cuentan_como_una():
    s = dasuten_txt.linea(_fila_dasuten(cantidad=0, sesion=0), "X")
    assert s[64:67] == "1  "
    assert s[67:79] == "248028200000"


def test_dasuten_recorta_los_textos_largos():
    fila = _fila_dasuten(
        beneficiario="123456789012", cod_nom="1234567890", prestador="a" * 40,
    )
    s = dasuten_txt.linea(fila, "d" * 50)
    assert len(s) == 129
    assert s[2:12] == "123456789 "
    assert s[15:24] == "12345678 "
    assert s[34:64] == "d" * 29 + " "
    assert s[79:109] == "A" * 29 + " "


def test_dasuten_el_archivo_en_latin1_conserva_el_ancho_con_acentos():
    s = dasuten_txt.linea(_fila_dasuten(prestador="Muñoz Núñez Ángel"), "EXTRACCIÓN")
    assert len(s.encode("latin-1")) == 129


def _factura() -> SimpleNamespace:
    return SimpleNamespace(periodo="202609", cod_obr=243)


def test_omint_libro_lleva_titulo_encabezados_y_una_fila_por_prestacion():
    filas = [
        {
            "nom_ape_p": "VALLEJOS  AGUSTIN", "beneficiario": "1850333402029",
            "fecha_practica": datetime.date(2026, 8, 21), "cod_nom": "420132",
            "importe_total": Decimal("31200.00"), "especialidad": "PEDIATRIA", "autorizacion": "0",
        },
        {
            "nom_ape_p": "ZAYAS HECTOR", "beneficiario": "181924920003",
            "fecha_practica": None, "cod_nom": "080709",
            "importe_total": Decimal("126164.25"), "especialidad": None, "autorizacion": "00300267153",
        },
    ]
    contenido, total = omint_xlsx._libro(_factura(), filas, {"420132": "CONSULTA PEDIATRICA"})
    assert total == Decimal("157364.25")
    ws = load_workbook(io.BytesIO(contenido)).active
    assert ws["A1"].value == "COLEGIO MEDICO CORRIENTES"
    assert ws["A2"].value == "OBRA SOCIAL: OMINT - PERIODO: 9 - 2026"
    assert tuple(c.value for c in ws[3]) == omint_xlsx.ENCABEZADOS
    primera, segunda = (tuple(c.value for c in ws[n]) for n in (4, 5))
    assert primera[0] == "VALLEJOS AGUSTIN"  # espacios dobles colapsados
    assert primera[1] == "1850333402029"  # el beneficiario va como texto
    assert primera[2] == datetime.datetime(2026, 8, 21)
    assert primera[3:6] == ("420132", "CONSULTA PEDIATRICA", 31200.0)
    assert primera[6] == "PEDIATRIA"
    assert primera[7] in (None, "")  # autorización 0 = sin autorización
    assert segunda[2] is None  # sin fecha
    assert segunda[4] in (None, "")  # código sin descripción
    assert segunda[7] == "00300267153"  # el token es la autorización
    assert ws.max_row == 5
