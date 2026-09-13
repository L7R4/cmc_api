"""Lookup de precio end-to-end tras eliminar NNE, contra datos reales de dev.

Solo lectura: llama a `service.lookup_precio` directo (sin pasar por HTTP/auth) sobre
IDs reales que ya existían antes de esta migración. Ver conftest.py — mismo criterio
de "no crea ni borra nada" del resto de `tests/`.
"""
import datetime

import pytest

from app.modules.nomenclador import service

FECHA = datetime.date(2026, 9, 1)

# 030801 PAROTIDECTOMIA TOTAL, OS 62: dos variantes NE reales con precio distinto
# por especialidad (Cirugía General $4.500.000 vs ORL $3.554.494,58).
PAROTIDECTOMIA_ID = 897
OS_62 = 62
MEDICO_CIRUGIA_GENERAL = 19    # NRO_ESPECIALIDAD=7
MEDICO_ORL = 135               # NRO_ESPECIALIDAD=36
MEDICO_SIN_ESPECIALIDAD = 2358  # NRO_ESPECIALIDAD=0

# 010607, OS 77: ex-NNE expandida a 3 variantes NE (29/30/47) con el MISMO precio —
# el caso típico de la migración (la especialidad no discriminaba nada, solo
# restringía). Valores 75832/75833/75834, ver mig_nne_map.
EX_NNE_ID = 586
OS_77 = 77
MEDICO_ESP_29 = 38   # NRO_ESPECIALIDAD=29
MEDICO_ESP_30 = 154  # NRO_ESPECIALIDAD=30


@pytest.mark.asyncio
async def test_ne_discrimina_precio_por_especialidad(db):
    r_cirugia = await service.lookup_precio(
        nomenclador_id=PAROTIDECTOMIA_ID, obra_social_nro=OS_62, fecha=FECHA,
        medico_id=MEDICO_CIRUGIA_GENERAL, db=db,
    )
    assert r_cirugia.origen == "NE"
    assert r_cirugia.variante_especialidad_id == 7
    assert r_cirugia.precio_total == 4_500_000

    r_orl = await service.lookup_precio(
        nomenclador_id=PAROTIDECTOMIA_ID, obra_social_nro=OS_62, fecha=FECHA,
        medico_id=MEDICO_ORL, db=db,
    )
    assert r_orl.origen == "NE"
    assert r_orl.variante_especialidad_id == 36
    assert r_orl.precio_total != r_cirugia.precio_total


@pytest.mark.asyncio
async def test_medico_sin_especialidad_habilitada_es_rechazado(db):
    with pytest.raises(service.LookupError):
        await service.lookup_precio(
            nomenclador_id=PAROTIDECTOMIA_ID, obra_social_nro=OS_62, fecha=FECHA,
            medico_id=MEDICO_SIN_ESPECIALIDAD, db=db,
        )


@pytest.mark.asyncio
async def test_ex_nne_expandida_da_el_mismo_precio_a_cualquier_habilitada(db):
    """El caso que motivó eliminar NNE: la especialidad no elegía entre precios
    distintos, solo restringía quién puede facturar. Verifica que la expansión no
    haya introducido una diferencia de precio donde antes (con una sola fila NNE)
    no la había."""
    r_29 = await service.lookup_precio(
        nomenclador_id=EX_NNE_ID, obra_social_nro=OS_77, fecha=FECHA,
        medico_id=MEDICO_ESP_29, db=db,
    )
    r_30 = await service.lookup_precio(
        nomenclador_id=EX_NNE_ID, obra_social_nro=OS_77, fecha=FECHA,
        medico_id=MEDICO_ESP_30, db=db,
    )
    assert r_29.origen == "NE"
    assert r_30.origen == "NE"
    assert r_29.variante_especialidad_id == 29
    assert r_30.variante_especialidad_id == 30
    assert r_29.precio_total == r_30.precio_total
