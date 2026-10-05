"""Una fila que factura únicamente el concepto ayudante se tipifica como Honorarios individuales."""
from decimal import Decimal as D

from app.modules.facturacion.service import es_solo_ayudante


def test_solo_ayudante_por_montos():
    assert es_solo_ayudante(D("0"), D("0"), D("5000"))
    assert not es_solo_ayudante(D("1000"), D("0"), D("5000"))   # el cirujano cobra honorarios
    assert not es_solo_ayudante(D("0"), D("300"), D("5000"))    # o gastos
    assert not es_solo_ayudante(D("0"), D("0"), D("0"))         # nada que cobrar


def test_solo_ayudante_en_fila_sin_precio_se_lee_del_concepto():
    # Cargada sin precio: los montos están en 0 y `sin_valorizar` dice qué concepto es.
    assert es_solo_ayudante(D("0"), D("0"), D("0"), "A")
    assert not es_solo_ayudante(D("0"), D("0"), D("0"), "HG")
    assert not es_solo_ayudante(D("0"), D("0"), D("0"), "HGA")
