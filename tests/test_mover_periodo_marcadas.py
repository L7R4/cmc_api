"""Pasar de período: las prestaciones marcadas (auditadas) se quedan en su período.

Sesión sin commit (`commit` → `flush`).
"""
import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.db.models import DetalleFacturacionCMC
from app.modules.facturacion import service
from app.modules.facturacion.schemas import MoverPeriodoPayload

OS = "32760"  # cod_obr es SMALLINT; OS que no existe


@pytest.fixture
def s(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)
    return db


def _prestacion(revisado: bool) -> DetalleFacturacionCMC:
    return DetalleFacturacionCMC(
        periodo="202609", version=1, estado="A", revisado=revisado,
        cod_obr=OS, cod_med="1084", cod_nom="420101", cantidad=1, sesion=1, porc=100,
        nro_orden=0, tpo_funcion="M", manual="N", ayudante=Decimal("0"),
        honorarios=Decimal("100"), gastos=Decimal("0"), coseguro=Decimal("0"),
        importe_total=Decimal("100"), dni_p="1", nom_ape_p="PRUEBA",
        fecha_practica=datetime.date(2026, 9, 1), usuario="1084",
    )


@pytest.mark.asyncio
async def test_no_mueve_marcadas_y_si_las_no_marcadas(s):
    marcada, libre = _prestacion(True), _prestacion(False)
    s.add_all([marcada, libre])
    await s.flush()

    with pytest.raises(HTTPException) as exc:
        await service.mover_prestaciones_periodo(s, MoverPeriodoPayload(
            cod_obra=OS, periodo_origen="202609", direccion="siguiente",
            ids=[marcada.id_detalle_prestaciones, libre.id_detalle_prestaciones],
        ))
    assert exc.value.status_code == 409
    assert exc.value.detail["marcadas"] == [marcada.id_detalle_prestaciones]

    r = await service.mover_prestaciones_periodo(s, MoverPeriodoPayload(
        cod_obra=OS, periodo_origen="202609", direccion="siguiente",
        ids=[libre.id_detalle_prestaciones],
    ))
    assert r.ids_movidos == [libre.id_detalle_prestaciones] and r.periodo_destino == "202610"
    assert marcada.periodo == "202609"
