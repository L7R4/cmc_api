"""Datos de cabecera del detalle de una factura (encabezado del listado de prestaciones).

El legacy guardó `0000-00-00` en las fechas sin dato; el driver las devuelve como texto y
rompían la validación de la respuesta (500 en el listado). Sólo se informan fechas reales.
"""
import datetime

from sqlalchemy import select

from app.db.models import FacturacionCMC
from app.modules.facturacion import service


def test_fecha_o_none_descarta_el_cero_legacy():
    assert service._fecha_o_none("0000-00-00") is None
    assert service._fecha_o_none(None) is None
    assert service._fecha_o_none(datetime.date(2026, 10, 1)) == datetime.date(2026, 10, 1)


async def test_cabecera_de_cada_factura_trae_solo_fechas_reales(db):
    facturas = (await db.execute(select(FacturacionCMC).limit(60))).scalars().all()
    assert facturas
    for f in facturas:
        d = await service._datos_cabecera_factura(db, f)
        for k in ("fecha_cierre", "fecha_envio", "fecha_recepcion"):
            assert d[k] is None or isinstance(d[k], datetime.date), (f.id_prestaciones, k)
        assert isinstance(d["numeros_factura"], list) and isinstance(d["otras_versiones"], list)
        assert d["fecha_cierre"] is None or (f.estado or "").upper() in service.FACTURA_ESTADOS_CERRADOS
