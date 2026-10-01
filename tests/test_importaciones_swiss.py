"""Reglas propias del import de Swiss Medical.

Lo que distingue a Swiss de Prevención y por lo tanto merece prueba: la
traducción de códigos y la matrícula con provincia. Los casos salen del
reporte real de agosto/septiembre de 2026 (1.127 prácticas, 70 códigos
distintos, 182 matrículas, 4 de otra provincia).
"""
import pytest

from app.modules.importaciones.swiss import homologador, servicio


# ── Homologación de códigos ───────────────────────────────────────────────────
#
# Swiss manda ocho dígitos y el Colegio usa seis. En el archivo real hay 37
# códigos que ya vienen en formato Colegio y 33 en el de Swiss.


@pytest.mark.parametrize(
    "swiss,colegio",
    [
        ("42010100", "420101"),  # Consulta médica en consultorio
        ("42013200", "420132"),  # Consulta pediatría
        ("18010401", "180104"),  # Ecografía transvaginal
        ("42011200", "420112"),  # Consulta traumatología
    ],
)
def test_los_de_ocho_digitos_se_traducen(swiss, colegio):
    assert homologador.homologar(swiss) == colegio


@pytest.mark.parametrize("codigo", ["180106", "150106", "110311", "300122"])
def test_los_de_seis_pasan_tal_cual(codigo):
    assert homologador.homologar(codigo) == codigo
    assert homologador.fue_truncado(codigo) is False


def test_dos_codigos_de_swiss_pueden_caer_en_el_mismo_del_colegio():
    # El caso que obliga a marcar la fila: no sabemos si Swiss los factura
    # distinto, así que la revisión tiene que poder verlo.
    assert homologador.homologar("42010100") == homologador.homologar("42010104")
    assert homologador.fue_truncado("42010104") is True


def test_un_codigo_no_numerico_no_se_adivina():
    # Mejor que la fila quede sin cotizar y se vea, a inventar una traducción.
    assert homologador.homologar("ABC123XY") == ""
    assert homologador.homologar("") == ""


def test_las_excepciones_ganan_sobre_la_regla_general():
    homologador.EXCEPCIONES["99999999"] = "420999"
    try:
        assert homologador.homologar("99999999") == "420999"
        assert homologador.fue_truncado("99999999") is False
    finally:
        homologador.EXCEPCIONES.pop("99999999")


# ── Matrícula con provincia ───────────────────────────────────────────────────
#
# Swiss antepone la letra de provincia: "W-3972". Las que no son de Corrientes
# no se pueden resolver contra `listado_medico` sólo por el número.


@pytest.mark.parametrize(
    "matricula,esperada",
    [("W-3972", "W"), ("w-3972", "W"), ("N-1234", "N"), ("3972", ""), ("", "")],
)
def test_lectura_de_la_provincia(matricula, esperada):
    assert servicio._provincia(matricula) == esperada


def test_corrientes_es_la_provincia_propia():
    assert servicio.CORRIENTES == "W"
    assert servicio._provincia("W-3972") == servicio.CORRIENTES
