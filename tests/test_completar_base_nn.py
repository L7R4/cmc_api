"""Herramienta "Completar nomenclador NN" (`service.completar_base_nn`).

Completa lo que falta (7 galenos base en $0 + un NN por código candidato) y NO
toca lo que ya existe: lo informa. Sesión sin commit (`commit` → `flush`).
"""
import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models.catalogs import ObrasSociales
from app.db.models.nomenclador_cmc import Galeno, HistorialPrecioCodigo, Valor
from app.modules.nomenclador import service

OS = 990_081
VIG = datetime.date(1900, 1, 1)


@pytest.fixture
def s(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)
    return db


async def _os(db):
    db.add(ObrasSociales(NRO_OBRASOCIAL=OS, OBRA_SOCIAL="PRUEBA COMPLETAR NN", cuit="0"))
    await db.flush()


async def _nn_activos(db) -> list[Valor]:
    return list((await db.execute(select(Valor).where(
        Valor.obra_social_nro == OS, Valor.origen == "NN", Valor.estado == "activo",
    ))).scalars())


@pytest.mark.asyncio
async def test_os_vacia_crea_todo_en_cero_con_historial(s):
    await _os(s)
    r = await service.completar_base_nn(OS, VIG, s)

    assert len(r["galenos_creados"]) == 7 and not r["galenos_existentes"]
    assert r["nn_creados"] == r["total_candidatos"] > 0 and not r["errores"]
    nn = await _nn_activos(s)
    assert len(nn) == r["nn_creados"]
    assert all(v.vigencia_desde == VIG for v in nn)
    # Todo valor nuevo tiene su historial (invariante de persistir_valor), en $0.
    hist = (await s.execute(select(HistorialPrecioCodigo).where(
        HistorialPrecioCodigo.valores_id.in_([v.id for v in nn])
    ))).scalars().all()
    assert len(hist) == len(nn) and all(h.precio_total == Decimal("0") for h in hist)


@pytest.mark.asyncio
async def test_respeta_galenos_y_nn_existentes(s):
    await _os(s)
    # La OS ya tiene UN galeno base con precio real.
    s.add(Galeno(obra_social_nro=OS, codigo="galeno_quirurgico", nombre="Galeno Quirúrgico",
                 nivel=None, vigencia_desde=datetime.date(2026, 1, 1),
                 valor_unitario=Decimal("1500"), activo=True))
    await s.flush()
    primera = await service.completar_base_nn(OS, VIG, s)
    assert [g["codigo"] for g in primera["galenos_existentes"]] == ["galeno_quirurgico"]
    assert len(primera["galenos_creados"]) == 6
    g = await service.buscar_galeno_vigente(s, OS, "galeno_quirurgico", None)
    assert g.valor_unitario == Decimal("1500")  # no se tocó

    ids_antes = {v.id for v in await _nn_activos(s)}
    segunda = await service.completar_base_nn(OS, VIG, s)
    assert segunda["nn_creados"] == 0 and segunda["nn_existentes"] == len(ids_antes)
    assert not segunda["galenos_creados"] and len(segunda["galenos_existentes"]) == 7
    assert {v.id for v in await _nn_activos(s)} == ids_antes  # nada cerrado ni recreado


@pytest.mark.asyncio
async def test_sembrar_nomenclador_nuevo_usa_el_mismo_camino(s):
    await _os(s)
    r = await service.sembrar_nomenclador_nuevo(OS, VIG, s)
    assert r["galenos_creados"] == 7 and r["creados"] == r["total_candidatos"] and r["recreados"] == 0
