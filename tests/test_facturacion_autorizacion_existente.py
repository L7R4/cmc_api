"""Aviso de Nº de autorización ya cargado en la O.S. (`service.autorizacion_existente`).

Sesión sin commit; prestaciones sintéticas en una O.S. que no existe (29998).
"""
import pytest

from app.db.models import DetalleFacturacionCMC
from app.modules.facturacion import service

OS = "29998"


@pytest.fixture
def s(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)
    return db


def _fila(periodo, autorizacion, estado="A", version=1):
    return DetalleFacturacionCMC(
        periodo=periodo, version=version, estado=estado, cod_obr=OS, cod_med="1", cod_nom="420101",
        cantidad=1, sesion=1, porc=100, nro_orden="0", manual="A", tpo_funcion="H", autorizacion=autorizacion,
        honorarios=0, gastos=0, ayudante=0, importe_total=0, dni_p="1", nom_ape_p="PRUEBA",
        usuario="test",
    )


@pytest.mark.asyncio
async def test_agrupa_por_periodo_exacto_y_sin_anuladas(s):
    filas = [_fila("209901", "AU-1"), _fila("209901", "AU-1"), _fila("209903", "AU-1"),
             _fila("209902", "AU-1", estado="X"), _fila("209902", "AU-12")]
    s.add_all(filas)
    await s.flush()

    out = await service.autorizacion_existente(s, OS, " AU-1 ")
    assert [(a.periodo, a.cantidad) for a in out] == [("209903", 1), ("209901", 2)]

    # Excluye la prestación que se está editando.
    out = await service.autorizacion_existente(s, OS, "AU-1", excluir_id=filas[2].id_detalle_prestaciones)
    assert [a.periodo for a in out] == ["209901"]

    # Otra O.S. y vacío: nada.
    assert await service.autorizacion_existente(s, "29997", "AU-1") == []
    assert await service.autorizacion_existente(s, OS, "  ") == []
