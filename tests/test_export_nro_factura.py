"""El Nº de factura cargado al cerrar aparece en el encabezado del detalle."""
from app.db.models import FacturacionCMC
from app.modules.facturacion.export.encabezado import _linea_facturas


def _factura(**kw) -> FacturacionCMC:
    base = dict(tipo_factura="", nro_factura="", tipo_factura_2="", nro_factura_2="",
                tipo_factura_3="", nro_factura_3="")
    return FacturacionCMC(**{**base, **kw})


def test_tipo_y_numero():
    assert _linea_facturas(_factura(tipo_factura="A", nro_factura="00031-00000453")) == \
        "Tipo y Nº de Factura/s: A - 00031-00000453"


def test_numero_sin_tipo_igual_se_muestra():
    assert _linea_facturas(_factura(nro_factura="00031-00000453")) == \
        "Tipo y Nº de Factura/s: 00031-00000453"


def test_sin_numero_muestra_guion():
    assert _linea_facturas(_factura(tipo_factura="A")) == "Tipo y Nº de Factura/s: -"
