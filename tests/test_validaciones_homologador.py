"""Homologación de códigos: con qué código se le habla a cada obra social.

Homologar cambia **sólo lo que se transmite**. El código del Colegio —el que
eligió el médico— sigue siendo el que se cotiza, el que se graba en `cod_nom` y
el que se factura, y es el que decide la habilitación por especialidad.

Antes esto vivía en `cliente.py` como `SUSTITUCIONES`, el alta cotizaba el
sustituto y el buscador de códigos el del Colegio: un médico con especialidad 41
elegía `420302`, veía $ 0,00 y se le facturaban $ 27.560 de `420351`.
"""
import pytest

from app.modules.validaciones.core.contrato import Contexto, ValidadorOS
from app.modules.validaciones.obras.sancor import homologador
from app.modules.validaciones.obras.sancor.homologador import _validar, homologar


# ── Las tres homologaciones vigentes ──────────────────────────────────────────

@pytest.mark.parametrize(
    "codigo, especialidad, se_envia",
    [
        ("420302", 41, "420351"),
        ("420130", 33, "305001"),
        # Encontrada en `grabar_prestacion_Sancor.php`, viva desde marzo de 2026.
        ("420305", 15, "420101"),
    ],
)
def test_homologa_con_la_especialidad_principal(codigo, especialidad, se_envia):
    envio, colegio = homologar(codigo, especialidad)
    assert envio == se_envia
    assert colegio == codigo, "el código del Colegio se devuelve para la traza"


@pytest.mark.parametrize("codigo", ["420302", "420130", "420305"])
def test_otra_especialidad_no_homologa(codigo):
    """La homologación es por especialidad: sin ella el código va tal cual."""
    assert homologar(codigo, 99) == (codigo, None)
    assert homologar(codigo, None) == (codigo, None)


def test_codigo_que_no_esta_en_la_tabla_va_tal_cual():
    """Lo que no figura en el archivo no tiene homologación y sigue el flujo
    normal — es el default, no un caso de borde."""
    assert homologar("420351", 41) == ("420351", None)
    assert homologar("999999", None) == ("999999", None)


def test_070660_quedo_fuera_de_la_tabla():
    """Los dos caminos del legacy discrepan (el del médico no homologa, el del
    Colegio sí) y el precio difiere 3x. Se resuelve con el Colegio antes de
    agregarlo."""
    assert "070660" not in homologador.HOMOLOGACIONES
    assert homologar("070660", 16) == ("070660", None)


# ── Reglas de resolución ──────────────────────────────────────────────────────

def test_especialidad_none_es_el_default():
    tabla = {"111": [{"codigo_homologado": "999", "especialidad": None}]}
    _validar(tabla)
    assert _resolver(tabla, "111", 41) == ("999", "111")
    assert _resolver(tabla, "111", None) == ("999", "111")


def test_la_especialidad_especifica_le_gana_al_default():
    tabla = {
        "111": [
            {"codigo_homologado": "888", "especialidad": None},
            {"codigo_homologado": "777", "especialidad": 41},
        ]
    }
    _validar(tabla)
    assert _resolver(tabla, "111", 41) == ("777", "111")
    assert _resolver(tabla, "111", 33) == ("888", "111"), "cae al default"


def _resolver(tabla, codigo, especialidad):
    """`homologar` contra una tabla armada en el test."""
    original = homologador.HOMOLOGACIONES
    homologador.HOMOLOGACIONES = tabla
    try:
        return homologar(codigo, especialidad)
    finally:
        homologador.HOMOLOGACIONES = original


# ── El archivo se edita a mano: tiene que romper al arrancar ──────────────────

@pytest.mark.parametrize(
    "tabla, motivo",
    [
        ({"111": []}, "sin entradas"),
        ({"111": [{"especialidad": 41}]}, "sin codigo_homologado"),
        ({"111": [{"codigo_homologado": "111", "especialidad": 41}]}, "a sí mismo"),
        (
            {"111": [{"codigo_homologado": "999", "especialidad": "41"}]},
            "especialidad como string",
        ),
        (
            {
                "111": [
                    {"codigo_homologado": "999", "especialidad": 41},
                    {"codigo_homologado": "888", "especialidad": 41},
                ]
            },
            "especialidad duplicada",
        ),
        (
            {
                "111": [
                    {"codigo_homologado": "999", "especialidad": None},
                    {"codigo_homologado": "888", "especialidad": None},
                ]
            },
            "dos defaults",
        ),
    ],
)
def test_tabla_ambigua_no_arranca(tabla, motivo):
    """Elegir la primera entrada en silencio sería peor: se factura mal y nadie
    se entera."""
    with pytest.raises(RuntimeError):
        _validar(tabla)


def test_la_tabla_real_es_valida():
    _validar(homologador.HOMOLOGACIONES)


# ── El contrato ───────────────────────────────────────────────────────────────

def test_el_default_del_contrato_es_identidad():
    """Las obras sociales sin homologador no tienen que hacer nada."""
    assert ValidadorOS.homologar(object(), "420302", 41) == ("420302", None)


def test_especialidad_principal_es_el_slot_1():
    class MedicoFalso:
        NRO_ESPECIALIDAD = 41
        NRO_ESPECIALIDAD2 = 16

    ctx = Contexto.__new__(Contexto)
    ctx.medico = MedicoFalso()
    assert ctx.especialidad_principal() == 41

    MedicoFalso.NRO_ESPECIALIDAD = 0
    assert ctx.especialidad_principal() is None, "0 es 'sin especialidad' en el legacy"
