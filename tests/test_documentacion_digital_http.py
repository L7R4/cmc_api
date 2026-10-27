"""Documentación digital por HTTP: los endpoints del Colegio y del médico, los permisos y el PDF.

Datos 100% sintéticos (obra social 29996, médico 999970001, período 203002), sembrados y borrados por el
fixture. Corre contra la base de desarrollo, como `test_facturacion_publicado.py`. En Windows los tests con
`TestClient` se corren de a uno (el event loop se cierra entre tests); por eso cada escenario es un solo test.
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
from pypdf import PdfReader

from app.core.config import settings
from app.core.security import create_access_token

COD_OBRA = "29996"
NRO_MEDICO = 999970001
PERIODO = "203002"
CODIGO = "999871"  # sintético: el catálogo real no lo tiene; se crea acá
CARPETA_ARCHIVOS = Path("uploads") / "validaciones" / str(NRO_MEDICO)


def _conn():
    return pymysql.connect(
        host=settings.MYSQL_HOST, port=settings.MYSQL_PORT or 3306,
        user=settings.MYSQL_USER, password=settings.MYSQL_PASS, database=settings.MYSQL_DB,
        autocommit=True,
    )


def _pdf(paginas=1) -> bytes:
    pdf = FPDF()
    for i in range(paginas):
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, f"pagina {i + 1}")
    return bytes(pdf.output())


def _jpg() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (300, 200), "red").save(b, format="JPEG")
    return b.getvalue()


@pytest.fixture
def escenario():
    """Obra social con documentación digital, un código que la exige, un médico y una prestación abierta."""
    con = _conn()
    with con.cursor() as cur:
        cur.execute(
            "INSERT INTO obras_sociales (NRO_OBRASOCIAL, OBRA_SOCIAL, cuit, contactos, direcciones, documentacion_digital) "
            "VALUES (%s, 'PRUEBA DOC DIGITAL', '0', '[]', '[]', 1)", (COD_OBRA,),
        )
        cur.execute(
            "INSERT INTO nm_nomenclador (codigo, descripcion, activo) VALUES (%s, 'PRUEBA DOC', 1)", (CODIGO,),
        )
        nom_id = cur.lastrowid
        cur.execute(
            "INSERT INTO nm_codigo_obra_social (obra_social_nro, nomenclador_id, codigo, estado, requiere_documentacion) "
            "VALUES (%s, %s, %s, 'activo', 1)", (COD_OBRA, nom_id, CODIGO),
        )
        cur.execute(
            "INSERT INTO listado_medico (NRO_SOCIO, NOMBRE, MATRICULA_PROV, estado_cuenta, hashed_password, conceps_espec) "
            "VALUES (%s, 'PRUEBA, DOC', 7777, 'activo', 'x', %s)", (NRO_MEDICO, '{"conceps": [], "espec": []}'),
        )
        cur.execute(
            "INSERT INTO facturacion (id_cliente, tipo_factura, nro_factura, tipo_factura_2, nro_factura_2, "
            " tipo_factura_3, nro_factura_3, periodo, cod_obr, fecha, fecha_envio, fecha_recep, importe, afip, "
            " usuario, estado, estado_doctor, version) "
            "VALUES (0, '', '', '', '', '', '', %s, %s, %s, %s, %s, 0, 'N', '1', 'A', 'A', 1)",
            (PERIODO, COD_OBRA, datetime.date(2030, 2, 1), datetime.date(2030, 2, 1), datetime.date(2030, 2, 1)),
        )
        cur.execute("SELECT id_prestaciones FROM facturacion WHERE cod_obr = %s", (COD_OBRA,))
        factura_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO detalle_facturacion "
            "(periodo, cod_med, nro_orden, cod_obr, cod_nom, tpo_funcion, sesion, cantidad, porc, honorarios, gastos, "
            " ayudante, importe_total, manual, estado, usuario, origen_carga, validacion_estado, validacion_anulada, "
            " version, nom_ape_p, dni_p, fecha_practica) "
            "VALUES (%s, %s, 0, %s, %s, 'HO', 1, 1, 100, 100, 0, 0, 100, 'A', 'A', %s, 'medico', 'cargada', 0, 1, "
            " 'PACIENTE DE PRUEBA', '1', %s)",
            (PERIODO, str(NRO_MEDICO), COD_OBRA, CODIGO, str(NRO_MEDICO), datetime.date(2030, 2, 3)),
        )
        prestacion_id = cur.lastrowid
    try:
        yield SimpleNamespaceLike(factura=factura_id, prestacion=prestacion_id)
    finally:
        with con.cursor() as cur:
            cur.execute("DELETE FROM detalle_facturacion WHERE cod_obr = %s", (COD_OBRA,))  # borra sus documentos (cascada)
            cur.execute("DELETE FROM facturacion WHERE cod_obr = %s", (COD_OBRA,))
            cur.execute("DELETE FROM nm_codigo_obra_social WHERE obra_social_nro = %s", (COD_OBRA,))
            cur.execute("DELETE FROM nm_nomenclador WHERE codigo = %s", (CODIGO,))
            cur.execute("DELETE FROM listado_medico WHERE NRO_SOCIO = %s", (NRO_MEDICO,))
            cur.execute("DELETE FROM obras_sociales WHERE NRO_OBRASOCIAL = %s", (COD_OBRA,))
        con.close()
        shutil.rmtree(CARPETA_ARCHIVOS, ignore_errors=True)


class SimpleNamespaceLike:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _headers(scopes, sub="1", role="admin"):
    token = create_access_token(sub=sub, uid=1, scopes=scopes, role=role)
    return {"Authorization": f"Bearer {token}"}


COLEGIO = ["facturacion:cargar", "facturacion:leer", "facturacion:cerrar", "export:generar", "medico:leer"]


def test_el_colegio_gestiona_los_documentos_y_audita(app, escenario):
    p, hdr = escenario.prestacion, _headers(COLEGIO)
    base = f"/api/facturacion/prestaciones/{p}/documentos"
    with TestClient(app, raise_server_exceptions=False) as c:
        # Sin documentos no se audita.
        r = c.patch("/api/facturacion/prestaciones/revisado", json={"marcados": [p]}, headers=hdr)
        assert r.status_code == 409, r.text

        # Subir un PDF y una foto (multipart).
        r = c.post(base, files=[("documentos", ("orden.pdf", _pdf(2), "application/pdf")),
                                ("documentos", ("bono.jpg", _jpg(), "image/jpeg"))], headers=hdr)
        assert r.status_code == 200, r.text
        docs = r.json()
        assert [d["nombre"] for d in docs] == ["orden.pdf", "bono.jpg"] and all(d["origen"] == "colegio" for d in docs)
        assert all(d["url"].startswith("/api/archivos/validaciones/") for d in docs)

        # Algo que no es un PDF ni una imagen se rechaza por su contenido.
        r = c.post(base, files=[("documentos", ("x.pdf", b"MZ no soy un pdf", "application/pdf"))], headers=hdr)
        assert r.status_code == 415

        # Reordenar, reemplazar y quitar.
        a, b = docs[0]["id"], docs[1]["id"]
        r = c.patch(f"{base}/orden", json={"ids": [b, a]}, headers=hdr)
        assert [d["id"] for d in r.json()] == [b, a]
        r = c.put(f"{base}/{a}", files={"archivo": ("nuevo.png", _jpg(), "image/jpeg")}, headers=hdr)
        assert r.status_code == 200 and {d["nombre"] for d in r.json()} == {"bono.jpg", "nuevo.png"}

        # Con documentos ya se audita.
        r = c.patch("/api/facturacion/prestaciones/revisado", json={"marcados": [p]}, headers=hdr)
        assert r.status_code == 200, r.text

        # El detalle de la factura trae los números de la documentación digital.
        d = c.get(f"/api/facturacion/facturas/{escenario.factura}/detalle", headers=hdr).json()
        assert d["documentacion_digital"] is True and d["sin_auditar"] == 0
        fila = d["prestadores"][0]["prestaciones"][0]
        assert fila["requiere_documentacion"] is True and fila["documentos_count"] == 2

        # Quitar el último documento (Colegio): se puede, y la prestación vuelve a «sin auditar».
        for doc in c.get(base, headers=hdr).json():
            r = c.delete(f"{base}/{doc['id']}", headers=hdr)
            assert r.status_code == 200
        d = c.get(f"/api/facturacion/facturas/{escenario.factura}/detalle", headers=hdr).json()
        assert d["sin_auditar"] == 1 and d["prestadores"][0]["prestaciones"][0]["revisado"] is False


def test_el_pdf_de_documentacion_se_bloquea_con_pendientes_y_sale_cuando_esta_listo(app, escenario):
    p, hdr = escenario.prestacion, _headers(COLEGIO)
    url_pdf = f"/api/facturacion/facturas/{escenario.factura}/export/documentacion.pdf"
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get(url_pdf, headers=hdr)
        assert r.status_code == 409 and r.json()["detail"]["sin_documentos"] == 1

        c.post(f"/api/facturacion/prestaciones/{p}/documentos",
               files=[("documentos", ("orden.pdf", _pdf(2), "application/pdf")),
                      ("documentos", ("bono.jpg", _jpg(), "image/jpeg"))], headers=hdr)
        r = c.get(url_pdf, headers=hdr)  # con documentos pero sin auditar
        assert r.status_code == 409 and r.json()["detail"]["sin_auditar"] == 1

        c.patch("/api/facturacion/prestaciones/revisado", json={"marcados": [p]}, headers=hdr)
        r = c.get(url_pdf, headers=hdr)
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
        paginas = PdfReader(io.BytesIO(r.content)).pages
        assert len(paginas) == 3  # el PDF de 2 páginas + la foto
        assert "PRUEBA, DOC" in paginas[0].extract_text() and "Socio 999970001" in paginas[0].extract_text()

        # Y la factura no se cierra mientras haya algo sin auditar.
        c.patch("/api/facturacion/prestaciones/revisado", json={"desmarcados": [p]}, headers=hdr)
        r = c.post("/api/facturacion/cierre", data={"cod_obra": COD_OBRA, "periodo": PERIODO}, headers=hdr)
        assert r.status_code == 409 and r.json()["detail"]["tipo"] == "sin_auditar"
        prev = c.get("/api/facturacion/cierre/preview", params={"cod_obra": COD_OBRA, "periodo": PERIODO}, headers=hdr).json()
        assert prev["documentacion_digital"] is True and prev["sin_auditar"] == 1


def test_el_medico_sube_sus_documentos_y_no_puede_dejar_la_prestacion_sin_ellos(app, escenario):
    p = escenario.prestacion
    medico = _headers(["validacion:cargar"], sub=str(NRO_MEDICO), role="medico")
    base = f"/api/validaciones/prestaciones/{p}/documentos"
    with TestClient(app, raise_server_exceptions=False) as c:
        # Un médico no entra a los endpoints del Colegio (necesita facturacion:cargar).
        assert c.get(f"/api/facturacion/prestaciones/{p}/documentos", headers=medico).status_code == 403

        r = c.post(base, files=[("documentos", ("orden.pdf", _pdf(), "application/pdf"))], headers=medico)
        assert r.status_code == 200, r.text
        salida = r.json()
        assert salida["documentacion"] == "completa" and salida["documentos"][0]["origen"] == "medico"

        # Es el único documento y el código lo exige: no se puede quitar.
        doc_id = salida["documentos"][0]["id"]
        assert c.delete(f"{base}/{doc_id}", headers=medico).status_code == 409

        # Pero sí cambiarlo por otro.
        r = c.put(f"{base}/{doc_id}", files={"archivo": ("otro.jpg", _jpg(), "image/jpeg")}, headers=medico)
        assert r.status_code == 200 and r.json()["documentos"][0]["nombre"] == "otro.jpg"

        # Otro médico no puede tocar los de este.
        otro = _headers(["validacion:cargar"], sub="999970002", role="medico")
        assert c.get(base, headers=otro).status_code in (403, 404)
