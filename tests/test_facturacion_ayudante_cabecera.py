"""El ayudante de un equipo cotiza con el médico de cabecera.

La especialidad que exige el código (habilitación y variante NE) es la del que
hace la práctica, no la de quien lo asiste: carga, edición y recálculo tienen que
pedir el precio de la fila del ayudante con el médico de la cabeza del equipo.

Se espía `resolver_precio` para ver con qué médico se cotiza cada fila. Sesión
sin commit; factura sintética en el período 209912 (no existe).
"""
import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import DetalleFacturacionCMC, ListadoMedico
from app.modules.facturacion import recotizar, service
from app.modules.facturacion.schemas import PrestacionItem, PrestacionUpdate

D = Decimal
OS = ""  # lo elige `_dos_medicos`
PERIODO = "209912"
CODIGO = ""  # lo elige `_dos_medicos`
FECHA = datetime.date.today()


@pytest.fixture
def s(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)

    async def _no_rollback():
        return None

    monkeypatch.setattr(db, "rollback", _no_rollback)
    return db


@pytest.fixture
def cotizados(monkeypatch):
    """NRO_SOCIO del médico con el que se pidió cada precio, en orden."""
    vistos: list[int] = []
    real = service.resolver_precio

    async def espia(db, cod_obra, medico, *a, **kw):
        vistos.append(int(medico.NRO_SOCIO))
        return await real(db, cod_obra, medico, *a, **kw)

    monkeypatch.setattr(service, "resolver_precio", espia)
    return vistos


RESOLVER_PRECIO = service.resolver_precio  # sin espiar, para elegir el escenario


async def _dos_medicos(db) -> tuple[ListadoMedico, ListadoMedico]:
    """Un código que paga ayudante en alguna O.S., un cirujano que lo cotiza con precio y
    otro socio que ya fue ayudante en ese código. Deja el código y la O.S. en `CODIGO` y `OS`."""
    global CODIGO, OS
    M = DetalleFacturacionCMC

    async def socio(nro):
        return (await db.execute(
            select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == int(nro))
        )).scalar_one_or_none()

    pares = (await db.execute(
        select(M.cod_obr, M.cod_nom).where(M.ayudante > 0, M.estado == "A", M.manual == "A")
        .distinct().limit(200)
    )).all()
    for os_, cod in pares:
        ayudantes = (await db.execute(select(M.cod_med).where(
            M.cod_obr == os_, M.cod_nom == cod, M.ayudante > 0,
        ).distinct().limit(5))).scalars().all()
        cirujanos = (await db.execute(select(M.cod_med).where(
            M.cod_obr == os_, M.cod_nom == cod, M.honorarios > 0,
        ).distinct().limit(5))).scalars().all()
        for c in cirujanos:
            med = await socio(c)
            if med is None:
                continue
            p = await RESOLVER_PRECIO(db, os_, med, cod, FECHA)
            if not (p.admitido and not p.sin_precio and p.ayudante > 0):
                continue
            for a in ayudantes:
                ayu = await socio(a)
                if ayu is not None and ayu.NRO_SOCIO != med.NRO_SOCIO:
                    CODIGO, OS = cod, os_
                    return med, ayu
    pytest.skip("No hay un código con ayudante cotizable.")


async def _cargar_equipo(db, cirujano, ayudante):
    await service._ensure_factura_abierta(db, OS, PERIODO, "test")
    await db.flush()
    item = dict(cod_nomenclador=CODIGO, fecha_practica=FECHA, tipo_calculo="A")
    return await service._insertar_prestaciones(
        db, cod_obra=OS, periodo=PERIODO, version_destino=1, usuario="test", actor="colegio",
        items=[
            PrestacionItem(cod_medico=str(cirujano.NRO_SOCIO), honorarios=D("1"), gastos=D("1"), **item),
            PrestacionItem(cod_medico=str(ayudante.NRO_SOCIO), ayudante=D("1"), **item),
        ],
    )


@pytest.mark.asyncio
async def test_alta_de_equipo_cotiza_el_ayudante_con_el_cirujano(s, cotizados):
    cirujano, ayudante = await _dos_medicos(s)
    out = await _cargar_equipo(s, cirujano, ayudante)
    assert cotizados == [cirujano.NRO_SOCIO, cirujano.NRO_SOCIO]

    # La fila del ayudante sigue siendo del ayudante: cobra él.
    fila = await s.get(DetalleFacturacionCMC, out.ids[1])
    assert int(fila.cod_med) == ayudante.NRO_SOCIO and fila.ayudante > 0


@pytest.mark.asyncio
async def test_agregar_ayudante_a_un_equipo_guardado_cotiza_con_la_cabeza(s, cotizados):
    cirujano, ayudante = await _dos_medicos(s)
    await service._ensure_factura_abierta(s, OS, PERIODO, "test")
    out = await service._insertar_prestaciones(
        s, cod_obra=OS, periodo=PERIODO, version_destino=1, usuario="test", actor="colegio",
        items=[PrestacionItem(cod_medico=str(cirujano.NRO_SOCIO), cod_nomenclador=CODIGO,
                              fecha_practica=FECHA, honorarios=D("1"), gastos=D("1"))],
    )
    cotizados.clear()
    await service._insertar_prestaciones(
        s, cod_obra=OS, periodo=PERIODO, version_destino=1, usuario="test", actor="colegio",
        items=[PrestacionItem(cod_medico=str(ayudante.NRO_SOCIO), cod_nomenclador=CODIGO,
                              fecha_practica=FECHA, ayudante=D("1"),
                              grupo_equipo_id=out.ids[0])],
    )
    assert cotizados == [cirujano.NRO_SOCIO]


@pytest.mark.asyncio
async def test_editar_y_recalcular_el_ayudante_cotizan_con_el_cirujano(s, cotizados):
    cirujano, ayudante = await _dos_medicos(s)
    out = await _cargar_equipo(s, cirujano, ayudante)
    fila = await s.get(DetalleFacturacionCMC, out.ids[1])

    cotizados.clear()
    await service.editar_prestacion(s, fila.id_detalle_prestaciones, PrestacionUpdate(ayudante=D("1")))
    assert cotizados == [cirujano.NRO_SOCIO]

    # El recálculo usa su propio caché, que llama a `service.resolver_precio`.
    cotizados.clear()
    await recotizar.recotizar_fila(s, fila)
    assert cotizados == [cirujano.NRO_SOCIO]


@pytest.mark.asyncio
async def test_ayudante_suelto_cotiza_con_su_propio_medico(s, cotizados):
    _, ayudante = await _dos_medicos(s)
    await service._ensure_factura_abierta(s, OS, PERIODO, "test")
    await service._insertar_prestaciones(
        s, cod_obra=OS, periodo=PERIODO, version_destino=1, usuario="test", actor="colegio",
        items=[PrestacionItem(cod_medico=str(ayudante.NRO_SOCIO), cod_nomenclador=CODIGO,
                              fecha_practica=FECHA, ayudante=D("1"))],
    )
    assert cotizados == [ayudante.NRO_SOCIO]
