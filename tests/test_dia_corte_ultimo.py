"""`dia_corte = 31` es "último día del mes": se recorta al largo de cada mes."""
import datetime

import pytest
from pydantic import ValidationError

from app.modules.catalogs.schemas import ObraSocialCreate, ObraSocialUpdate
from app.common.dates import DIA_CORTE_ULTIMO, normalizar_dia_corte
from app.modules.facturacion.service import (
    fecha_a_periodo,
    periodo_medico_por_fecha,
    ventana_periodo,
)

D = datetime.date


@pytest.mark.parametrize("hoy, esperado", [
    (D(2026, 10, 30), "202610"),
    (D(2026, 10, 31), "202610"),  # el día de corte se sigue cargando en el período viejo
    (D(2026, 11, 1), "202611"),
    (D(2026, 11, 30), "202611"),  # noviembre tiene 30: ese es su último día
    (D(2026, 12, 1), "202612"),
    (D(2027, 2, 28), "202702"),
    (D(2027, 3, 1), "202703"),
    (D(2028, 2, 29), "202802"),   # bisiesto
])
def test_periodo_medico_ultimo_dia(hoy, esperado):
    assert periodo_medico_por_fecha(hoy, DIA_CORTE_ULTIMO) == esperado


def test_corte_fijo_sigue_igual():
    # 20: el médico carga el 20 en el período viejo y pasa al siguiente el 21.
    assert periodo_medico_por_fecha(D(2026, 10, 20), 20) == "202610"
    assert periodo_medico_por_fecha(D(2026, 10, 21), 20) == "202611"


@pytest.mark.parametrize("dia", [28, 29, 30, 31])
def test_desde_el_28_es_ultimo_dia(dia):
    # Un 30 viejo (así se pedía "fin de mes") también corta el 31/oct, no el 30.
    assert normalizar_dia_corte(dia) == DIA_CORTE_ULTIMO
    assert periodo_medico_por_fecha(D(2026, 10, 31), dia) == "202610"
    assert periodo_medico_por_fecha(D(2026, 11, 1), dia) == "202611"
    assert normalizar_dia_corte(27) == 27


@pytest.mark.parametrize("fecha, esperado", [
    (D(2026, 10, 30), "202610"),
    (D(2026, 10, 31), "202611"),  # el último día ya es del período que cierra a fin del mes siguiente
    (D(2026, 11, 29), "202611"),
    (D(2026, 11, 30), "202612"),
])
def test_fecha_a_periodo_ultimo_dia(fecha, esperado):
    assert fecha_a_periodo(fecha, DIA_CORTE_ULTIMO) == esperado


def test_ventana_periodo_ultimo_dia():
    assert ventana_periodo("202611", DIA_CORTE_ULTIMO) == (D(2026, 10, 31), D(2026, 11, 30))
    assert ventana_periodo("202603", DIA_CORTE_ULTIMO) == (D(2026, 2, 28), D(2026, 3, 31))
    assert ventana_periodo("202803", DIA_CORTE_ULTIMO) == (D(2028, 2, 29), D(2028, 3, 31))
    # Cortes fijos: sin cambios.
    assert ventana_periodo("202611", 20) == (D(2026, 10, 20), D(2026, 11, 20))


def test_schema_normaliza_desde_el_28_y_rechaza_1():
    for dia in (28, 30, 31):
        assert ObraSocialCreate(nro_obra_social=1, nombre="X", dia_corte=dia).dia_corte == 31
        assert ObraSocialUpdate(dia_corte=dia).dia_corte == 31
    assert ObraSocialUpdate(dia_corte=27).dia_corte == 27
    assert ObraSocialUpdate().dia_corte is None
    for malo in (0, 1, 32):
        with pytest.raises(ValidationError):
            ObraSocialUpdate(dia_corte=malo)
