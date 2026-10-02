"""Con clínica (Sanatorio u Honorarios individuales) los gastos van siempre en 0."""
import pytest

from app.modules.facturacion import service


@pytest.mark.parametrize(
    "tipo, esperado",
    [
        (service.TIPO_SANATORIO, True),
        (service.CATEGORIA_HONORARIOS_INDIVIDUALES, True),
        ("Practica", False),
        ("Consulta", False),
        (None, False),
    ],
)
def test_gasto_forzado_a_cero_por_tipo(tipo, esperado):
    assert service.gasto_forzado_a_cero(tipo) is esperado
