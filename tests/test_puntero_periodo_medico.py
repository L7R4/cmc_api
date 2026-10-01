"""El corte de una OS sin puntero propio nunca avanza el puntero GLOBAL.

Sesión sin commit (`commit` → `flush`): el fixture `db` descarta todo al cerrar.
"""
import datetime

import pytest
from sqlalchemy import select

from app.db.models.catalogs import ObrasSociales
from app.db.models.cmc_facturacion import PeriodoMedicoActual
from app.modules.facturacion import service

OS_SIN_PUNTERO = 990_051


@pytest.fixture
def s(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)
    return db


class _Hoy(datetime.date):
    @classmethod
    def today(cls):
        return cls(2026, 9, 6)


async def _global(db) -> PeriodoMedicoActual:
    return (await db.execute(
        select(PeriodoMedicoActual).where(PeriodoMedicoActual.obra_social_id.is_(None))
    )).scalar_one()


@pytest.mark.asyncio
async def test_os_con_corte_propio_no_avanza_el_global(s, monkeypatch):
    s.add(ObrasSociales(NRO_OBRASOCIAL=OS_SIN_PUNTERO, OBRA_SOCIAL="PRUEBA PUNTERO",
                        MARCA="S", cuit="0", dia_corte=5))
    await s.flush()
    g = await _global(s)
    g.periodo = "202609"
    await s.flush()
    monkeypatch.setattr(service.datetime, "date", _Hoy)

    periodo = await service.asegurar_periodo_medico_vigente(s, str(OS_SIN_PUNTERO))

    assert periodo == "202610"  # día 6 con corte 5 → ya pasó al siguiente
    assert (await _global(s)).periodo == "202609"  # el global no se tocó
    propio = (await s.execute(select(PeriodoMedicoActual).where(
        PeriodoMedicoActual.obra_social_id == OS_SIN_PUNTERO
    ))).scalar_one()
    assert propio.periodo == "202610"


@pytest.mark.asyncio
async def test_asegurar_puntero_propio_copia_el_global(s):
    g = await _global(s)
    propio = await service.asegurar_puntero_propio(s, OS_SIN_PUNTERO)
    assert propio.obra_social_id == OS_SIN_PUNTERO and propio.periodo == g.periodo
    assert await service.asegurar_puntero_propio(s, OS_SIN_PUNTERO) is propio
