"""CBU y medio de cobro: validación (app/common/cbu.py) y clasificación del informe de pago.

Los CBU son sintéticos: se arman con el mismo algoritmo del BCRA, no son cuentas reales.
"""
import pytest

from app.common.cbu import (
    DatosBancariosInvalidos,
    es_cbu_valido,
    es_cuit_valido,
    resolver_alta,
    resolver_cambio,
)
from app.modules.pagos.service import _clasificar_para_informe

P1 = (7, 1, 3, 9, 7, 1, 3)
P2 = (3, 9, 7, 1, 3, 9, 7, 1, 3, 9, 7, 1, 3)


def armar_cbu(entidad: str, sucursal: str = "0001", cuenta: str = "1234567890123") -> str:
    b1 = entidad + sucursal
    d1 = (10 - sum(int(a) * p for a, p in zip(b1, P1)) % 10) % 10
    d2 = (10 - sum(int(a) * p for a, p in zip(cuenta, P2)) % 10) % 10
    return f"{b1}{d1}{cuenta}{d2}"


SANTANDER = armar_cbu("072")
GALICIA = armar_cbu("007", cuenta="9876543210987")
CUIT_OK = "20123456786"      # 20-12345678-6 (dígito verificador correcto)
CUIT_30 = "30715678906"


def test_cuits_de_prueba_son_validos():
    assert es_cuit_valido(CUIT_OK)
    assert es_cuit_valido(CUIT_30)
    assert not es_cuit_valido("20123456780")


def test_cbu_valido():
    assert es_cbu_valido(SANTANDER) and es_cbu_valido(GALICIA)
    assert not es_cbu_valido(SANTANDER[:-1] + str((int(SANTANDER[-1]) + 1) % 10))   # DV del 2º bloque
    assert not es_cbu_valido(SANTANDER[:7] + str((int(SANTANDER[7]) + 1) % 10) + SANTANDER[8:])  # DV del 1º bloque
    assert not es_cbu_valido(SANTANDER[:21])        # 21 dígitos
    assert not es_cbu_valido("0" * 10)              # cuenta HSBC vieja
    assert not es_cbu_valido("20123456786")         # un CUIT
    assert not es_cbu_valido(None) and not es_cbu_valido("")


def test_alta_clasifica_el_cbu_viejo_por_entidad():
    assert resolver_alta(cbu=SANTANDER) == {"cbu_santander": SANTANDER, "cbu_otro_banco": None, "medio_cobro": "santander"}
    assert resolver_alta(cbu=GALICIA) == {"cbu_santander": None, "cbu_otro_banco": GALICIA, "medio_cobro": "otros_bancos"}
    assert resolver_alta() == {"cbu_santander": None, "cbu_otro_banco": None, "medio_cobro": "cheque"}


def test_alta_acepta_espacios_y_guiones():
    con_formato = f"{SANTANDER[:3]} {SANTANDER[3:8]}-{SANTANDER[8:]}"
    assert resolver_alta(cbu_santander=con_formato)["cbu_santander"] == SANTANDER


def test_alta_rechaza_cbu_mal_cargado():
    with pytest.raises(DatosBancariosInvalidos):
        resolver_alta(cbu_santander=GALICIA)            # de otro banco en el campo de Santander
    with pytest.raises(DatosBancariosInvalidos):
        resolver_alta(cbu_otro_banco=SANTANDER)         # de Santander en el campo de otro banco
    with pytest.raises(DatosBancariosInvalidos):
        resolver_alta(cbu_santander=SANTANDER[:-1] + "0" if SANTANDER[-1] != "0" else SANTANDER[:-1] + "1")
    with pytest.raises(DatosBancariosInvalidos):
        resolver_alta(cbu="123")


def test_alta_el_medio_exige_su_cbu():
    with pytest.raises(DatosBancariosInvalidos):
        resolver_alta(medio_cobro="santander")
    with pytest.raises(DatosBancariosInvalidos):
        resolver_alta(medio_cobro="otros_bancos", cbu_santander=SANTANDER)
    assert resolver_alta(medio_cobro="cheque", cbu_santander=SANTANDER)["medio_cobro"] == "cheque"
    with pytest.raises(DatosBancariosInvalidos):
        resolver_alta(medio_cobro="efectivo")


def test_cambio_sin_cambios_no_escribe_nada():
    actual = {"cbu_santander": SANTANDER, "cbu_otro_banco": None, "medio_cobro": "santander"}
    assert resolver_cambio(actual, {"cbu_santander": SANTANDER, "medio_cobro": "santander"}) == {}
    assert resolver_cambio(actual, {"cbu_otro_banco": ""}) == {}


def test_cambio_tolera_el_valor_viejo_que_no_es_cbu_si_no_cambia():
    actual = {"cbu_santander": None, "cbu_otro_banco": "1234567890", "medio_cobro": "cheque"}
    assert resolver_cambio(actual, {"cbu_otro_banco": "1234567890", "medio_cobro": "cheque"}) == {}
    # pero no se puede elegir «otros bancos» con un valor que no es CBU
    with pytest.raises(DatosBancariosInvalidos):
        resolver_cambio(actual, {"medio_cobro": "otros_bancos"})
    # y si lo reemplaza, tiene que ser un CBU de verdad
    with pytest.raises(DatosBancariosInvalidos):
        resolver_cambio(actual, {"cbu_otro_banco": "1234567891"})
    assert resolver_cambio(actual, {"cbu_otro_banco": GALICIA, "medio_cobro": "otros_bancos"}) == {
        "cbu_otro_banco": GALICIA, "medio_cobro": "otros_bancos"}


def test_cambio_no_deja_el_medio_apuntando_a_una_cuenta_vacia():
    actual = {"cbu_santander": SANTANDER, "cbu_otro_banco": None, "medio_cobro": "santander"}
    with pytest.raises(DatosBancariosInvalidos):
        resolver_cambio(actual, {"cbu_santander": None})          # borra el CBU pero sigue cobrando por Santander
    assert resolver_cambio(actual, {"cbu_santander": None, "medio_cobro": "cheque"}) == {"cbu_santander": None, "medio_cobro": "cheque"}


@pytest.mark.parametrize(
    "medio,cs,co,cuit,esperado",
    [
        ("santander", SANTANDER, None, CUIT_OK, "santander"),
        ("santander", SANTANDER, None, CUIT_30, "santander_30"),
        ("otros_bancos", None, GALICIA, CUIT_OK, "otros_bancos"),
        ("otros_bancos", None, GALICIA, CUIT_30, "otros_bancos_30"),
        ("cheque", SANTANDER, GALICIA, CUIT_OK, "cheques"),         # elige cheque aunque tenga cuentas
        ("cheque", None, None, None, "cheques"),                    # el cheque no pide CUIT
        ("santander", None, GALICIA, CUIT_OK, "observados"),        # elige Santander y no tiene CBU de Santander
        ("otros_bancos", None, "1234567890", CUIT_OK, "observados"),  # valor viejo que no es CBU
        ("santander", SANTANDER, None, "", "observados"),           # sin CUIT
        ("santander", SANTANDER, None, "20123456780", "observados"),  # CUIT con dígito verificador malo
        (None, SANTANDER, None, CUIT_OK, "cheques"),                # sin medio = cheque
    ],
)
def test_informe_cada_socio_cae_en_un_solo_archivo(medio, cs, co, cuit, esperado):
    destino, motivo, _cbu, _cuit = _clasificar_para_informe(medio, cs, co, cuit)
    assert destino == esperado
    assert (motivo is not None) == (esperado == "observados")


def test_informe_devuelve_el_cbu_del_medio_elegido():
    _, _, cbu, _ = _clasificar_para_informe("otros_bancos", SANTANDER, GALICIA, CUIT_OK)
    assert cbu == GALICIA
    _, _, cbu, _ = _clasificar_para_informe("santander", SANTANDER, GALICIA, CUIT_OK)
    assert cbu == SANTANDER
