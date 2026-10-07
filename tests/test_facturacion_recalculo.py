"""Recálculo de precios (`facturacion/recotizar.py`).

Sesión sin commit (`commit` → `flush`, `rollback` → no-op para poder mirar las
filas después de una vista previa). Los montos esperados no se escriben a mano:
salen de `resolver_precio` con el mismo médico, código y fecha, así el test no
depende de cuánto valga hoy el código en la base.

Factura sintética: O.S. 103 en el período 209912 (no existe).
"""
import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db.models import DetalleFacturacionCMC, ListadoMedico
from app.modules.facturacion import recotizar, service
from app.modules.facturacion.recotizar import RecalculoIn, recalcular_precios, recotizar_fila

D = Decimal
OS = "103"
PERIODO = "209912"
CODIGO = "180104"  # NN, honorarios + gastos
FECHA = datetime.date(2026, 9, 10)


@pytest.fixture
def s(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)

    async def _no_rollback():
        return None

    monkeypatch.setattr(db, "rollback", _no_rollback)
    return db


async def _medico(db) -> ListadoMedico:
    """Un socio que ya facturó el código en la 103 (está habilitado y tiene precio)."""
    M = DetalleFacturacionCMC
    nros = (await db.execute(
        select(M.cod_med).where(M.cod_obr == OS, M.cod_nom == CODIGO, M.estado == "A").distinct().limit(20)
    )).scalars().all()
    for nro in nros:
        med = (await db.execute(select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == int(nro)))).scalar_one_or_none()
        if med is None:
            continue
        precio = await service.resolver_precio(db, OS, med, CODIGO, FECHA)
        if precio.admitido and not precio.sin_precio and precio.honorarios > 0:
            return med
    pytest.skip(f"No hay un socio con precio para {CODIGO} en la O.S. {OS}.")


async def _factura(db, estado="A"):
    # Igual que la crea el sistema al cargar la primera prestación del período.
    await service._ensure_factura_abierta(db, OS, PERIODO, "test")
    await db.flush()
    if estado != "A":
        cab = await service._get_factura(db, OS, PERIODO)
        cab.estado = estado
        await db.flush()


def _fila(med, **kw) -> DetalleFacturacionCMC:
    base = dict(
        periodo=PERIODO, version=1, estado="A", cod_obr=OS, cod_med=str(med.NRO_SOCIO),
        cod_nom=CODIGO, cantidad=1, sesion=1, porc=100, nro_orden=0, tpo_funcion="HG",
        manual="A", honorarios=D("1"), gastos=D("1"), ayudante=D("0"), coseguro=D("0"),
        importe_total=D("2"), dni_p="1", nom_ape_p="PRUEBA", fecha_practica=FECHA, usuario="test",
    )
    base.update(kw)
    return DetalleFacturacionCMC(**base)


async def _precio(db, med, fecha=FECHA):
    return await service.resolver_precio(db, OS, med, CODIGO, fecha, ignorar_ventana=True)


async def _correr(db, dry_run=False, codigo=None):
    return await recalcular_precios(db, RecalculoIn(cod_obra=OS, periodo=PERIODO, codigo=codigo, dry_run=dry_run))


@pytest.mark.asyncio
async def test_recotiza_con_cantidad_y_coseguro_como_la_carga(s):
    med = await _medico(s)
    await _factura(s)
    # Total con la fórmula de facturación: (1 + 1 − 0,5) × 2 × 1 = 3.
    row = _fila(med, cantidad=2, coseguro=D("0.5"), importe_total=D("3"))
    s.add(row)
    await s.flush()

    out = await _correr(s)
    p = await _precio(s, med)
    assert out.cambian == 1 and out.filas[0].id == row.id_detalle_prestaciones
    assert (row.honorarios, row.gastos) == (p.honorarios, p.gastos)
    cos = min(p.coseguro, p.honorarios + p.gastos)
    assert row.coseguro == cos
    assert row.importe_total == service.calcular_importe_total(p.honorarios, p.gastos, D("0"), 2, 1, coseguro=cos)

    # Idempotente: la segunda corrida no cambia nada.
    otra = await _correr(s)
    assert otra.cambian == 0 and otra.sin_cambios == 1


@pytest.mark.asyncio
async def test_fila_de_validacion_conserva_su_formula_y_su_coseguro(s):
    med = await _medico(s)
    await _factura(s)
    # Validaciones: (h + g) × cantidad − coseguro, una sola vez: (1 + 1) × 2 − 0,5 = 3,5.
    row = _fila(med, cantidad=2, coseguro=D("0.5"), importe_total=D("3.5"),
                validacion_estado="autorizada", ga_id=999999999)
    s.add(row)
    await s.flush()
    assert recotizar.formula_de(row) == recotizar.FORMULA_VALIDACION

    await _correr(s)
    p = await _precio(s, med)
    assert row.coseguro == D("0.5")  # el que pagó el afiliado, no el del nomenclador
    assert row.importe_total == (p.honorarios + p.gastos) * 2 - D("0.5")


@pytest.mark.asyncio
async def test_manual_y_total_raro_no_se_tocan(s):
    med = await _medico(s)
    await _factura(s)
    manual = _fila(med, manual="M", honorarios=D("777"), gastos=D("0"), importe_total=D("777"))
    raro = _fila(med, importe_total=D("999"))  # no sale de 1 + 1
    s.add_all([manual, raro])
    await s.flush()

    out = await _correr(s)
    assert out.cambian == 0 and out.omitidas == 2
    motivos = {o.id: o.motivo for o in out.omitidas_detalle}
    assert "manual" in motivos[manual.id_detalle_prestaciones]
    assert "total guardado" in motivos[raro.id_detalle_prestaciones]
    assert manual.honorarios == D("777") and raro.importe_total == D("999")


@pytest.mark.asyncio
async def test_vista_previa_no_graba_y_codigo_filtra(s):
    med = await _medico(s)
    await _factura(s)
    del_codigo = _fila(med)
    otro = _fila(med, cod_nom="420101")
    s.add_all([del_codigo, otro])
    await s.flush()

    previa = await _correr(s, dry_run=True, codigo=CODIGO)
    assert previa.total == 1 and previa.cambian == 1
    assert del_codigo.honorarios == D("1")  # no se aplicó

    await _correr(s, codigo=CODIGO)
    assert del_codigo.honorarios != D("1") and otro.honorarios == D("1")


@pytest.mark.asyncio
async def test_conserva_conceptos_y_rol_pediatra(s):
    med = await _medico(s)
    await _factura(s)
    solo_gastos = _fila(med, honorarios=D("0"), gastos=D("1"), importe_total=D("1"), tpo_funcion="G")
    pediatra = _fila(med, honorarios=D("1"), gastos=D("0"), importe_total=D("1"),
                     tpo_funcion="P", coseguro=D("0"))
    s.add_all([solo_gastos, pediatra])
    await s.flush()

    await _correr(s)
    assert solo_gastos.honorarios == D("0") and solo_gastos.gastos > 0
    assert pediatra.tpo_funcion == "P" and pediatra.coseguro == D("0") and pediatra.gastos == D("0")


@pytest.mark.asyncio
async def test_factura_cerrada_o_inexistente(s):
    with pytest.raises(HTTPException) as exc:
        await _correr(s)
    assert exc.value.status_code == 404
    await _factura(s, estado="C")
    with pytest.raises(HTTPException) as exc:
        await _correr(s)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_la_ventana_de_6_meses_solo_se_saltea_al_recotizar(s):
    med = await _medico(s)
    vieja = datetime.date.today() - datetime.timedelta(days=300)
    normal = await service.resolver_precio(s, OS, med, CODIGO, vieja)
    assert not normal.admitido and "6 meses" in (normal.motivo or "")
    recot = await service.resolver_precio(s, OS, med, CODIGO, vieja, ignorar_ventana=True)
    assert "6 meses" not in (recot.motivo or "")


@pytest.mark.asyncio
async def test_sin_fecha_cotiza_a_hoy(s):
    med = await _medico(s)
    row = _fila(med, fecha_practica=None)
    s.add(row)
    await s.flush()
    r = await recotizar_fila(s, row)
    assert r.fecha == datetime.date.today()


def test_el_cache_agrupa_fechas_por_tramo_de_vigencia():
    d = datetime.date
    cache = recotizar.CacheCotizacion([d(2026, 3, 1), d(2026, 8, 1)])
    assert cache.tramo(d(2026, 3, 5)) == cache.tramo(d(2026, 7, 31))  # misma vigencia
    assert cache.tramo(d(2026, 7, 31)) != cache.tramo(d(2026, 8, 1))  # cruza el corte
    assert recotizar.CacheCotizacion().tramo(d(2026, 3, 5)) == d(2026, 3, 5)  # sin cortes: fecha exacta


@pytest.mark.asyncio
async def test_la_memoria_por_corrida_se_apaga_al_terminar(s):
    from app.modules.nomenclador import service as service_nm
    with recotizar.memo_por_corrida(s):
        assert service_nm.MEMO_COTIZACION in s.info
    assert service_nm.MEMO_COTIZACION not in s.info


@pytest.mark.asyncio
async def test_fila_en_cero_sin_marca_deduce_los_conceptos_del_tpo_funcion(s):
    # Cargadas en $0 antes de que existiera la marca `sin_valorizar`.
    med = await _medico(s)
    await _factura(s)
    medico = _fila(med, honorarios=D("0"), gastos=D("0"), importe_total=D("0"), tpo_funcion="H", cantidad=3)
    ayudante = _fila(med, honorarios=D("0"), gastos=D("0"), importe_total=D("0"), tpo_funcion="A")
    s.add_all([medico, ayudante])
    await s.flush()
    assert recotizar.conceptos_de(medico) == "HG" and recotizar.conceptos_de(ayudante) == "A"

    await _correr(s)
    p = await _precio(s, med)
    # Médico: honorarios + gastos del precio, nunca el ayudante; total × cantidad.
    assert (medico.honorarios, medico.gastos, medico.ayudante) == (p.honorarios, p.gastos, D("0"))
    assert medico.importe_total == service.calcular_importe_total(
        p.honorarios, p.gastos, D("0"), 3, 1, coseguro=medico.coseguro,
    )
    # Ayudante: sólo la columna de ayudante.
    assert (ayudante.honorarios, ayudante.gastos, ayudante.ayudante) == (D("0"), D("0"), p.ayudante)


@pytest.mark.asyncio
async def test_revalorizar_toma_las_automaticas_sin_marca_y_cuenta_las_que_no_cambian(s):
    from app.modules.facturacion import revalorizar

    med = await _medico(s)
    await _factura(s)
    p = await _precio(s, med)
    vieja = _fila(med)  # precio viejo (1 + 1), sin marca
    al_dia = _fila(med, honorarios=p.honorarios, gastos=p.gastos, coseguro=min(p.coseguro, p.honorarios + p.gastos),
                   importe_total=service.calcular_importe_total(
                       p.honorarios, p.gastos, D("0"), 1, 1, coseguro=min(p.coseguro, p.honorarios + p.gastos)))
    manual = _fila(med, manual="M")
    s.add_all([vieja, al_dia, manual])
    await s.flush()

    out = await revalorizar.revalorizar(s, revalorizar.RevalorizarIn(cod_obra=OS, codigo=CODIGO, dry_run=True))
    ids = {i.id for i in out.items if i.estado == "revalorizada"}
    assert vieja.id_detalle_prestaciones in ids
    assert al_dia.id_detalle_prestaciones not in ids and manual.id_detalle_prestaciones not in ids
    assert out.sin_cambios >= 1
