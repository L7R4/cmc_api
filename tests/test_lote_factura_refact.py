"""Factura de la refacturación dentro del lote: tipo, número y un archivo (PATCH/PUT/DELETE .../factura_refact).

Datos 100% sintéticos (obras sociales 29995 y 29994, un pago 'C' del año 2030), sembrados y borrados por el
fixture. Corre contra la base de desarrollo. En Windows los tests con `TestClient` se corren de a uno (el event
loop se cierra entre tests), por eso todo el recorrido es un solo test.
"""
import datetime
import io
import shutil
from pathlib import Path

import pymysql
import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF
from PIL import Image

from app.core.config import settings
from app.core.security import create_access_token

OS = 29995
OTRA_OS = 29994
NRO_ORIGINAL = "00031-00001234"
PERIODO = "203003"


def _conn():
    return pymysql.connect(
        host=settings.MYSQL_HOST, port=settings.MYSQL_PORT or 3306,
        user=settings.MYSQL_USER, password=settings.MYSQL_PASS, database=settings.MYSQL_DB,
        autocommit=True,
    )


def _pdf() -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, "factura")
    return bytes(pdf.output())


def _png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (60, 40), "blue").save(b, format="PNG")
    return b.getvalue()


def _headers(scopes, role="liquidador"):
    return {"Authorization": f"Bearer {create_access_token(sub='1', uid=1, scopes=scopes, role=role)}"}


class _Esc:
    pass


@pytest.fixture
def escenario():
    con = _conn()
    esc = _Esc()
    lotes = []
    with con.cursor() as cur:
        for nro, nombre in ((OS, "PRUEBA LOTE FACT"), (OTRA_OS, "PRUEBA LOTE FACT 2")):
            cur.execute(
                "INSERT INTO obras_sociales (NRO_OBRASOCIAL, OBRA_SOCIAL, cuit, contactos, direcciones) "
                "VALUES (%s, %s, '0', '[]', '[]')", (nro, nombre),
            )
        for cod, nro_fac in ((OS, NRO_ORIGINAL), (OTRA_OS, "00099-00000777")):
            cur.execute(
                "INSERT INTO facturacion (id_cliente, tipo_factura, nro_factura, tipo_factura_2, nro_factura_2, "
                " tipo_factura_3, nro_factura_3, periodo, cod_obr, fecha, fecha_envio, fecha_recep, importe, afip, "
                " usuario, estado, estado_doctor, version) "
                "VALUES (0, 'A', %s, '', '', '', '', %s, %s, %s, %s, %s, 0, 'N', '1', 'C', 'C', 1)",
                (nro_fac, PERIODO, cod, datetime.date(2030, 3, 1), datetime.date(2030, 3, 1), datetime.date(2030, 3, 1)),
            )
            if cod == OS:
                esc.factura = cur.lastrowid
        cur.execute("INSERT INTO pago (anio, mes, descripcion, estado) VALUES (2030, 3, 'PRUEBA LOTE FACT', 'C')")
        esc.pago = cur.lastrowid

        def lote(tipo, estado, pago=None, factura=True):
            cur.execute(
                "INSERT INTO lote_ajuste (obra_social_id, mes_periodo, anio_periodo, tipo, estado, pago_id, factura_id) "
                "VALUES (%s, 3, 2030, %s, %s, %s, %s)", (OS, tipo, estado, pago, esc.factura if factura else None),
            )
            lotes.append(cur.lastrowid)
            return cur.lastrowid

        esc.ref_a = lote("refacturacion", "A")
        esc.ref_c = lote("refacturacion", "C")
        esc.ref_l = lote("refacturacion", "L", pago=esc.pago)
        esc.ref_ap = lote("refacturacion", "AP", pago=esc.pago)
        esc.normal = lote("normal", "A")
        esc.sin_factura = lote("sin_factura", "A", factura=False)
    try:
        yield esc
    finally:
        with con.cursor() as cur:
            cur.execute("DELETE FROM lote_ajuste WHERE obra_social_id IN (%s, %s)", (OS, OTRA_OS))
            cur.execute("DELETE FROM pago WHERE id = %s", (esc.pago,))
            cur.execute("DELETE FROM facturacion WHERE cod_obr IN (%s, %s)", (OS, OTRA_OS))
            cur.execute("DELETE FROM obras_sociales WHERE NRO_OBRASOCIAL IN (%s, %s)", (OS, OTRA_OS))
        con.close()
        for i in lotes:
            shutil.rmtree(Path("uploads") / "lotes" / str(i), ignore_errors=True)


LIQUIDADOR = ["lote:leer", "lote:editar", "pago:leer"]
MEDICO = ["facturacion:leer"]


def test_factura_de_la_refacturacion(app, escenario):
    e, hdr = escenario, _headers(LIQUIDADOR)
    base = lambda i: f"/api/lotes/snaps/{i}/factura_refact"
    with TestClient(app, raise_server_exceptions=False) as c:
        # Un lote nuevo no trae nada cargado.
        r = c.get(f"/api/lotes/snaps/{e.ref_a}", headers=hdr)
        assert r.status_code == 200, r.text
        assert r.json()["refact_nro_factura"] is None and r.json()["refact_documento_url"] is None
        assert r.json()["nro_factura"] == NRO_ORIGINAL  # el de la original, aparte

        # Validaciones del tipo y el número.
        assert c.patch(base(e.ref_a), json={"nro_factura": "00032-1"}, headers=hdr).status_code == 422   # falta el tipo
        assert c.patch(base(e.ref_a), json={"tipo_factura": "B"}, headers=hdr).status_code == 422        # falta el número
        assert c.patch(base(e.ref_a), json={"tipo_factura": "Z", "nro_factura": "1"}, headers=hdr).status_code == 422
        assert c.patch(base(e.ref_a), json={"tipo_factura": "A", "nro_factura": "9" * 21}, headers=hdr).status_code == 422

        # Se carga en abierto, cerrado y en el pago.
        for lote_id, nro in ((e.ref_a, "00032-00000100"), (e.ref_c, " 00032-00000101 "), (e.ref_l, "00032-00000102")):
            r = c.patch(base(lote_id), json={"tipo_factura": "B", "nro_factura": nro}, headers=hdr)
            assert r.status_code == 200, r.text
            assert r.json()["refact_tipo_factura"] == "B" and r.json()["refact_nro_factura"] == nro.strip()
            assert r.json()["advertencias"] == []

        # Aplicado (pago cerrado), normal y sin factura: no.
        for lote_id in (e.ref_ap, e.normal, e.sin_factura):
            r = c.patch(base(lote_id), json={"tipo_factura": "A", "nro_factura": "1"}, headers=hdr)
            assert r.status_code == 409, (lote_id, r.text)

        # Avisos (no bloquean): repetido en otra refacturación (comparando dígitos), en la factura que se
        # corrige, y NO en otra obra social.
        r = c.patch(base(e.ref_c), json={"tipo_factura": "B", "nro_factura": "0003200000100"}, headers=hdr)
        assert r.status_code == 200 and any(f"#{e.ref_a}" in a for a in r.json()["advertencias"]), r.text
        r = c.patch(base(e.ref_c), json={"tipo_factura": "A", "nro_factura": NRO_ORIGINAL}, headers=hdr)
        assert r.status_code == 200 and any("la factura que se corrige" in a for a in r.json()["advertencias"]), r.text
        r = c.patch(base(e.ref_c), json={"tipo_factura": "A", "nro_factura": "00099-00000777"}, headers=hdr)
        assert r.status_code == 200 and r.json()["advertencias"] == [], r.text
        assert r.json()["refact_nro_factura"] == "00099-00000777"  # igual se guardó

        # Archivo: PDF, reemplazo por PNG (el viejo se borra del disco), rechazos.
        r = c.put(base(e.ref_a) + "/documento", files={"archivo": ("factura.pdf", _pdf(), "application/pdf")}, headers=hdr)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["refact_documento_nombre"] == "factura.pdf"
        assert j["refact_documento_url"].startswith(f"/api/archivos/lotes/{e.ref_a}/")
        primero = Path("uploads") / "lotes" / str(e.ref_a) / j["refact_documento_url"].rsplit("/", 1)[1]
        assert primero.is_file()

        r = c.put(base(e.ref_a) + "/documento", files={"archivo": ("scan.png", _png(), "image/png")}, headers=hdr)
        assert r.status_code == 200 and r.json()["refact_documento_nombre"] == "scan.png", r.text
        assert not primero.exists()
        url = r.json()["refact_documento_url"]
        actual = Path("uploads") / "lotes" / str(e.ref_a) / url.rsplit("/", 1)[1]
        assert actual.is_file()

        assert c.put(base(e.ref_a) + "/documento", files={"archivo": ("x.pdf", b"MZ no soy un pdf", "application/pdf")},
                     headers=hdr).status_code == 415
        assert c.put(base(e.ref_a) + "/documento", files={"archivo": ("x.pdf", b"%PDF-1.4" + b"0" * (10 * 1024 * 1024), "application/pdf")},
                     headers=hdr).status_code == 413
        for lote_id in (e.ref_ap, e.normal, e.sin_factura):
            r = c.put(base(lote_id) + "/documento", files={"archivo": ("f.pdf", _pdf(), "application/pdf")}, headers=hdr)
            assert r.status_code == 409, (lote_id, r.text)
        assert actual.is_file()  # lo rechazado no tocó el archivo vigente

        # Quién puede ver el archivo: lote:leer sí; un médico (facturacion:leer solamente) no.
        r = c.get(url, headers=hdr)
        assert r.status_code == 200 and r.content == _png(), r.status_code
        assert c.get(url, headers=_headers(MEDICO, role="medico")).status_code == 403
        # Y quién puede cargar: sin lote:editar, 403.
        assert c.patch(base(e.ref_a), json={"tipo_factura": "A", "nro_factura": "7"},
                       headers=_headers(["lote:leer"])).status_code == 403
        assert c.put(base(e.ref_a) + "/documento", files={"archivo": ("f.pdf", _pdf(), "application/pdf")},
                     headers=_headers(MEDICO, role="medico")).status_code == 403

        # El listado y el resumen del pago traen el número nuevo.
        r = c.get(f"/api/lotes/snaps/lista?tipo=refacturacion&obra_social_id={OS}", headers=hdr)
        assert r.status_code == 200, r.text
        fila = next(x for x in r.json() if x["id"] == e.ref_a)
        assert fila["refact_nro_factura"] == "00032-00000100" and fila["refact_documento_url"] == url
        assert fila["nro_factura"] == NRO_ORIGINAL

        r = c.get(f"/api/pagos/{e.pago}/vista_previa", headers=hdr)
        assert r.status_code == 200, r.text
        item = next(x for x in r.json()["lotes"]["items"] if x["lote_id"] == e.ref_l)
        assert item["refact_tipo_factura"] == "B" and item["refact_nro_factura"] == "00032-00000102"
        assert item["nro_factura"] == NRO_ORIGINAL

        # Quitar el archivo: la fila y el disco; tipo y número quedan.
        r = c.delete(base(e.ref_a) + "/documento", headers=hdr)
        assert r.status_code == 200 and r.json()["refact_documento_url"] is None and r.json()["refact_nro_factura"] == "00032-00000100"
        assert not actual.exists()

        # Limpiar tipo y número.
        r = c.patch(base(e.ref_a), json={"tipo_factura": None, "nro_factura": None}, headers=hdr)
        assert r.status_code == 200 and r.json()["refact_nro_factura"] is None and r.json()["refact_tipo_factura"] is None

        # Eliminar el lote borra su carpeta de archivos.
        r = c.put(base(e.ref_a) + "/documento", files={"archivo": ("f.pdf", _pdf(), "application/pdf")}, headers=hdr)
        assert r.status_code == 200
        carpeta = Path("uploads") / "lotes" / str(e.ref_a)
        assert carpeta.is_dir()
        assert c.delete(f"/api/lotes/snaps/{e.ref_a}", headers=_headers(LIQUIDADOR + ["lote:eliminar"])).status_code == 204
        assert not carpeta.exists()
