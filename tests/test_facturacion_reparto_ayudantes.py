"""La ayudantía del código es el TOTAL de la práctica: al facturar se reparte entre los ayudantes.

El nomenclador guarda el valor entero (25 % de los honorarios del cirujano); el reparto se hace
sólo en facturación: alta del equipo, sumar/quitar un ayudante, PATCH y recálculo. Lo ya cerrado
(estado C/L) y las filas cargadas a mano no se tocan.

Sesión sin commit; factura sintética en el período 209912 (no existe).
"""
import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import DetalleFacturacionCMC, ListadoMedico
from app.db.models.medico import ESTADO_ACTIVO
from app.modules.facturacion import recotizar, service
from app.modules.facturacion.schemas import PrestacionItem, PrestacionUpdate

D = Decimal
PERIODO = "209912"
FECHA = datetime.date.today()
M = DetalleFacturacionCMC


# ── Parte pura ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("total,n,esperado", [
    ("164246.70", 1, ["164246.70"]),
    ("164246.70", 2, ["82123.35", "82123.35"]),
    ("100.00", 3, ["33.34", "33.33", "33.33"]),      # los centavos sobrantes, al primero
    ("0.01", 2, ["0.01", "0.00"]),
    ("100.00", 0, ["100.00"]),                        # n inválido → un solo ayudante
])
def test_reparto_suma_exacto(total, n, esperado):
    r = service.reparto_ayudante(D(total), n)
    assert r == [D(x) for x in esperado]
    assert sum(r) == D(total)


def test_anotar_reparto_no_toca_el_snapshot_original():
    snap = [{"concepto": "Honorarios", "subtotal": "10"}, {"concepto": "Ayudante", "subtotal": "5"}]
    out = service.anotar_reparto(snap, 2)
    assert out[1]["ayudantes"] == 2 and "ayudantes" not in snap[1] and "ayudantes" not in out[0]
    assert service.ayudante_total_de_snapshot(out) == D("5.00")   # el total sigue entero
    assert service.ayudante_total_de_snapshot(None) is None


# ── Con la base ──────────────────────────────────────────────────────────────
@pytest.fixture
def s(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)

    async def _no_rollback():
        return None

    monkeypatch.setattr(db, "rollback", _no_rollback)
    return db


async def _escenario(db):
    """(os, código, cirujano, ayudante1, ayudante2, precio total de ayudantía) de un código que
    paga ayudante y se puede cotizar con un cirujano que lo hace."""
    async def socio(nro):
        return (await db.execute(
            select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == int(nro))
        )).scalar_one_or_none()

    pares = (await db.execute(
        select(M.cod_obr, M.cod_nom).where(M.ayudante > 0, M.estado == "A", M.manual == "A")
        .distinct().limit(200)
    )).all()
    for os_, cod in pares:
        cirujanos = (await db.execute(select(M.cod_med).where(
            M.cod_obr == os_, M.cod_nom == cod, M.honorarios > 0,
        ).distinct().limit(5))).scalars().all()
        for c in cirujanos:
            med = await socio(c)
            if med is None:
                continue
            p = await service.resolver_precio(db, os_, med, cod, FECHA)
            if not (p.admitido and not p.sin_precio and not p.por_presupuesto and p.ayudante > 0):
                continue
            otros = (await db.execute(
                select(ListadoMedico).where(
                    ListadoMedico.NRO_SOCIO != med.NRO_SOCIO, ListadoMedico.estado_cuenta == ESTADO_ACTIVO,
                    ListadoMedico.es_organizacion == 0,
                ).limit(2)
            )).scalars().all()
            if len(otros) == 2:
                return os_, cod, med, otros[0], otros[1], p.ayudante
    pytest.skip("No hay un código con ayudante cotizable.")


def _ayudante(socio, cod, **extra):
    return PrestacionItem(cod_medico=str(socio.NRO_SOCIO), cod_nomenclador=cod, fecha_practica=FECHA,
                          tipo_calculo="A", ayudante=D("1"), **extra)


def _cirujano(socio, cod):
    return PrestacionItem(cod_medico=str(socio.NRO_SOCIO), cod_nomenclador=cod, fecha_practica=FECHA,
                          tipo_calculo="A", honorarios=D("1"), gastos=D("1"))


async def _alta(db, os_, items):
    await service._ensure_factura_abierta(db, os_, PERIODO, "test")
    await db.flush()
    return await service._insertar_prestaciones(
        db, cod_obra=os_, periodo=PERIODO, version_destino=1, usuario="test", actor="colegio", items=items,
    )


async def _filas(db, ids):
    return [await db.get(M, i) for i in ids]


@pytest.mark.asyncio
async def test_un_solo_ayudante_cobra_el_total(s):
    os_, cod, cir, a1, _, total = await _escenario(s)
    out = await _alta(s, os_, [_cirujano(cir, cod), _ayudante(a1, cod)])
    ay = (await _filas(s, out.ids))[1]
    assert ay.ayudante == service.quantize_money(total)


@pytest.mark.asyncio
async def test_dos_ayudantes_se_reparten_el_total(s):
    os_, cod, cir, a1, a2, total = await _escenario(s)
    out = await _alta(s, os_, [_cirujano(cir, cod), _ayudante(a1, cod), _ayudante(a2, cod)])
    _, x, y = await _filas(s, out.ids)
    assert x.ayudante + y.ayudante == service.quantize_money(total)
    assert abs(x.ayudante - y.ayudante) <= D("0.01")
    # El snapshot conserva el total y anota entre cuántos se repartió.
    assert service.ayudante_total_de_snapshot(x.calculo_snapshot) == service.quantize_money(total)
    assert [c["ayudantes"] for c in x.calculo_snapshot if c["concepto"] == "Ayudante"] == [2]


@pytest.mark.asyncio
async def test_sumar_y_quitar_un_ayudante_rebalancea_los_abiertos(s):
    os_, cod, cir, a1, a2, total = await _escenario(s)
    total = service.quantize_money(total)
    out = await _alta(s, os_, [_cirujano(cir, cod), _ayudante(a1, cod)])
    cabeza, primero = await _filas(s, out.ids)
    assert primero.ayudante == total

    # Se suma el 2.º al equipo guardado: el 1.º pasa a cobrar la mitad.
    out2 = await _alta(s, os_, [_ayudante(a2, cod, grupo_equipo_id=cabeza.id_detalle_prestaciones)])
    segundo = await s.get(M, out2.ids[0])
    await s.refresh(primero)
    assert primero.ayudante + segundo.ayudante == total
    assert primero.ayudante < total

    # Se anula el 2.º: el 1.º vuelve a cobrar el total.
    await service.anular_prestacion(s, segundo.id_detalle_prestaciones, motivo=historial.MOTIVO_EQUIPO_EDITADO)
    await s.refresh(primero)
    assert primero.ayudante == total
    assert primero.importe_total == service.calcular_importe_total(
        primero.honorarios, primero.gastos, primero.ayudante, primero.cantidad or 1, primero.sesion or 1,
        coseguro=primero.coseguro,
    )


@pytest.mark.asyncio
async def test_lo_cerrado_y_lo_manual_no_se_tocan(s):
    os_, cod, cir, a1, a2, total = await _escenario(s)
    total = service.quantize_money(total)
    out = await _alta(s, os_, [_cirujano(cir, cod), _ayudante(a1, cod)])
    cabeza, primero = await _filas(s, out.ids)

    primero.estado = "C"          # ya facturado/cerrado: queda como está
    await s.flush()
    await _alta(s, os_, [_ayudante(a2, cod, grupo_equipo_id=cabeza.id_detalle_prestaciones)])
    await s.refresh(primero)
    assert primero.ayudante == total

    # Una fila cargada a mano conserva su monto aunque haya más ayudantes.
    manual = PrestacionItem(cod_medico=str(a1.NRO_SOCIO), cod_nomenclador=cod, fecha_practica=FECHA,
                            tipo_calculo="M", ayudante=D("777.77"),
                            grupo_equipo_id=cabeza.id_detalle_prestaciones)
    out3 = await _alta(s, os_, [manual])
    assert (await s.get(M, out3.ids[0])).ayudante == D("777.77")


@pytest.mark.asyncio
async def test_recalcular_precios_respeta_el_reparto(s):
    os_, cod, cir, a1, a2, total = await _escenario(s)
    out = await _alta(s, os_, [_cirujano(cir, cod), _ayudante(a1, cod), _ayudante(a2, cod)])
    _, x, y = await _filas(s, out.ids)
    antes = (x.ayudante, y.ayudante)
    rx = await recotizar.recotizar_fila(s, x)
    ry = await recotizar.recotizar_fila(s, y)
    assert (rx.ayudante, ry.ayudante) == antes      # mismo precio, mismo reparto


@pytest.mark.asyncio
async def test_editar_el_ayudante_conserva_su_parte(s):
    os_, cod, cir, a1, a2, total = await _escenario(s)
    out = await _alta(s, os_, [_cirujano(cir, cod), _ayudante(a1, cod), _ayudante(a2, cod)])
    _, x, y = await _filas(s, out.ids)
    antes = (x.ayudante, y.ayudante)
    await service.editar_prestacion(s, y.id_detalle_prestaciones, PrestacionUpdate(ayudante=D("1")))
    await s.refresh(x)
    await s.refresh(y)
    assert (x.ayudante, y.ayudante) == antes


from app.modules.facturacion import historial  # noqa: E402  (al final: sólo lo usa un test)
