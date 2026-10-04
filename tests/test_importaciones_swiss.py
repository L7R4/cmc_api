"""Reglas propias del import de Swiss Medical.

Lo que distingue a Swiss de Prevención y por lo tanto merece prueba: la
matrícula con provincia (los códigos de Swiss van tal cual, sin traducir). Los
casos salen del reporte real de agosto/septiembre de 2026 (1.127 prácticas, 70 códigos
distintos, 182 matrículas, 4 de otra provincia).
"""
import pytest

from app.modules.importaciones.swiss import servicio


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
