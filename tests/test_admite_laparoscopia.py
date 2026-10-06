"""`admite_laparoscopia`: la API dice si un código se puede cotizar por vía laparoscópica.

El front lo usa para ofrecer (o no) el selector de vía, en vez de mirar la categoría del
catálogo (mal cargada en parte de los códigos). Por eso el flag tiene que ser EXACTAMENTE
lo que después pasa al cotizar por vía L: si dice True la cotización L funciona, si dice
False la cotización L la rechaza.

Solo lectura, sobre los mismos IDs reales de dev que `test_lookup_ne_post_nne.py`.
"""
import datetime

import pytest

from app.modules.nomenclador import service

FECHA = datetime.date(2026, 9, 1)

# (nomenclador_id, obra_social_nro, medico_id) — parotidectomía OS 62 y el ex-NNE OS 77.
CASOS = [
    (897, 62, 19),
    (586, 77, 38),
]


async def _cotiza_por_via_l(db, nom_id, os_nro, medico_id) -> bool:
    try:
        await service.lookup_precio(nom_id, os_nro, FECHA, medico_id, db, via="L")
        return True
    except service.LookupError:
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize("nom_id,os_nro,medico_id", CASOS)
async def test_el_flag_coincide_con_lo_que_pasa_al_cotizar_por_via_l(db, nom_id, os_nro, medico_id):
    esperado = await _cotiza_por_via_l(db, nom_id, os_nro, medico_id)

    r = await service.lookup_precio(
        nom_id, os_nro, FECHA, medico_id, db, con_admision_via=True,
    )
    assert r.via == "T"
    assert r.admite_laparoscopia is esperado


@pytest.mark.asyncio
@pytest.mark.parametrize("nom_id,os_nro,medico_id", CASOS)
async def test_sin_pedirlo_no_se_calcula(db, nom_id, os_nro, medico_id):
    """Los procesos en bloque no piden el flag: no se calcula y queda en False."""
    r = await service.lookup_precio(nom_id, os_nro, FECHA, medico_id, db)
    assert r.admite_laparoscopia is False


@pytest.mark.asyncio
@pytest.mark.parametrize("nom_id,os_nro,medico_id", CASOS)
async def test_cotizado_por_via_l_exitoso_admite(db, nom_id, os_nro, medico_id):
    if not await _cotiza_por_via_l(db, nom_id, os_nro, medico_id):
        pytest.skip("este código no admite laparoscopía")
    r = await service.lookup_precio(
        nom_id, os_nro, FECHA, medico_id, db, via="L", con_admision_via=True,
    )
    assert r.via == "L"
    assert r.admite_laparoscopia is True
