"""`nm_galenos.visible`: el toggle pisa todas las filas del (OS, código) y las
rotaciones de precio lo heredan. Sesión sin commit (`commit` → `flush`)."""
import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.db.models.nomenclador_cmc import Galeno
from app.modules.nomenclador import service
from app.modules.nomenclador.routes_galenos import _rotar_precio_galeno, cambiar_visibilidad_galeno
from app.modules.nomenclador.schemas import GalenoVisibilidadIn

OS = 990_071
COD = "galeno_prueba_visible"
VIG = datetime.date(2026, 7, 1)


@pytest.fixture
def s(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)
    return db


async def _niveles(db, n=2):
    for nivel in range(1, n + 1):
        db.add(Galeno(obra_social_nro=OS, codigo=COD, nombre="Galeno visible", nivel=nivel,
                      vigencia_desde=VIG, valor_unitario=Decimal("100"), activo=True))
    await db.flush()


@pytest.mark.asyncio
async def test_ocultar_pisa_todos_los_niveles_y_la_rotacion_lo_hereda(s):
    await _niveles(s)
    r = await cambiar_visibilidad_galeno(GalenoVisibilidadIn(obra_social_nro=OS, codigo=COD, visible=False), s)
    assert r.filas_actualizadas == 2

    g1 = await service.buscar_galeno_vigente(s, OS, COD, 1)
    assert g1.visible is False
    nuevo = await _rotar_precio_galeno(s, g1, Decimal("150"), datetime.date(2026, 8, 1))
    assert nuevo.id != g1.id and nuevo.visible is False

    await cambiar_visibilidad_galeno(GalenoVisibilidadIn(obra_social_nro=OS, codigo=COD, visible=True), s)
    assert (await service.buscar_galeno_vigente(s, OS, COD, 1)).visible is True


@pytest.mark.asyncio
async def test_galeno_inexistente_da_404(s):
    with pytest.raises(HTTPException) as e:
        await cambiar_visibilidad_galeno(GalenoVisibilidadIn(obra_social_nro=OS, codigo=COD, visible=False), s)
    assert e.value.status_code == 404
