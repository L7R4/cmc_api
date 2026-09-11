"""Reglas del origen tras eliminar NNE (ver docs/api/nomenclador_origenes_ne_nn.md).

NE ahora exige especialidad_id_colegio (identifica la variante); NN sigue sin ella.
Estas pruebas son puras: no tocan la base, solo `validar_reglas_origen` y
`prioridad_origen` de `app.modules.nomenclador`.
"""
import pytest

from app.modules.nomenclador import service
from app.modules.nomenclador.schemas import Origen, ValorCreate, validar_reglas_origen


def test_ne_exige_especialidad():
    with pytest.raises(ValueError, match="especialidad_id_colegio"):
        validar_reglas_origen("NE", None, por_presupuesto=False, es_galeno=True)


def test_ne_con_especialidad_es_valido():
    validar_reglas_origen("NE", 7, por_presupuesto=False, es_galeno=True)  # no debe lanzar


def test_nn_no_admite_especialidad():
    with pytest.raises(ValueError, match="especialidad_id_colegio"):
        validar_reglas_origen("NN", 7, por_presupuesto=False, es_galeno=True)


def test_nn_no_admite_por_presupuesto():
    with pytest.raises(ValueError, match="por_presupuesto"):
        validar_reglas_origen("NN", None, por_presupuesto=True, es_galeno=None)


def test_nn_exige_galeno():
    with pytest.raises(ValueError, match="galeno"):
        validar_reglas_origen("NN", None, por_presupuesto=False, es_galeno=False)


def test_origen_no_admite_nne():
    with pytest.raises(ValueError):
        Origen("NNE")


def test_valor_create_rechaza_nne_con_mensaje_explicativo():
    with pytest.raises(ValueError, match="NNE fue eliminado"):
        ValorCreate(
            obra_social_nro=1,
            nomenclador_id=1,
            origen="NNE",
            vigencia_desde="2026-01-01",
            componentes=[],
            por_presupuesto=True,
        )


def test_prioridad_ne_gana_a_nn():
    assert service.prioridad_origen("NE") < service.prioridad_origen("NN")


def test_prioridad_origen_desconocido_pierde_contra_todo():
    # Fail-safe: una fila NNE residual que sobreviva a la migración no debe poder
    # ganarle a NE ni a NN en el desempate del lookup.
    desconocida = service.prioridad_origen("NNE")
    assert desconocida > service.prioridad_origen("NE")
    assert desconocida > service.prioridad_origen("NN")
