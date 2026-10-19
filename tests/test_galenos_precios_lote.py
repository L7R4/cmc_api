"""Cambio de precio de varios galenos a la vez (`POST /api/galenos/actualizar_precios_lote`):
todo o nada, y un valor que usa varios de esos galenos se recalcula una sola vez.

Datos sembrados en la sesión y NUNCA commiteados: `commit` → `flush`, `rollback` → no-op;
el fixture `db` cierra la sesión sin commit. OS inexistente.
"""
import datetime
import json
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db.models.nomenclador_cmc import Galeno, HistorialPrecioCodigo, NomencladorCMC, Valor
from app.modules.nomenclador import service
from app.modules.nomenclador.routes_galenos import actualizar_precios_galenos_lote
from app.modules.nomenclador.schemas import GalenoPrecioLoteItem, GalenosPrecioLoteIn

OS = 990_098
NOMENCLADOR_ID = 897
VIG = datetime.date(2026, 7, 1)
NUEVA = datetime.date(2026, 8, 1)
COD_HON, COD_GAS = "galeno_prueba_lote_hon", "gasto_prueba_lote"


@pytest.fixture
def s(db, monkeypatch):
    async def _noop():
        return None

    monkeypatch.setattr(db, "commit", db.flush)
    monkeypatch.setattr(db, "rollback", _noop)
    return db


async def _sembrar(db) -> Valor:
    """Un valor NN cuyo honorario sale de un galeno y su gasto de otro."""
    hon = Galeno(obra_social_nro=OS, codigo=COD_HON, nombre="Galeno hon", nivel=None,
                 vigencia_desde=VIG, valor_unitario=Decimal("100"), activo=True)
    gas = Galeno(obra_social_nro=OS, codigo=COD_GAS, nombre="Gasto", nivel=None,
                 vigencia_desde=VIG, valor_unitario=Decimal("10"), activo=True)
    db.add_all([hon, gas])
    await db.flush()
    nom = await db.get(NomencladorCMC, NOMENCLADOR_ID)
    return await service.persistir_valor(
        db,
        Valor(obra_social_nro=OS, nomenclador_id=NOMENCLADOR_ID, origen="NN", codigo=nom.codigo,
              descripcion="NN", vigencia_desde=VIG, estado="activo"),
        [
            dict(concepto="Honorarios", galeno_id=hon.id, cantidad=Decimal("2"), orden=0),
            dict(concepto="Gastos", galeno_id=gas.id, cantidad=Decimal("3"), orden=1),
            dict(concepto="Ayudante", galeno_id=hon.id, cantidad=Decimal("0"), orden=2),
        ],
        motivo="carga_inicial", fecha_corte=None,
    )


def _body(*items, vigencia=NUEVA) -> GalenosPrecioLoteIn:
    return GalenosPrecioLoteIn(
        obra_social_nro=OS, vigencia_desde=vigencia,
        items=[GalenoPrecioLoteItem(codigo=c, nuevo_valor_unitario=Decimal(v)) for c, v in items],
    )


async def _vigente(db, codigo) -> Galeno:
    return (await db.execute(select(Galeno).where(
        Galeno.obra_social_nro == OS, Galeno.codigo == codigo, Galeno.vigencia_hasta.is_(None),
    ))).scalar_one()


async def _historial(db) -> list[HistorialPrecioCodigo]:
    return list((await db.execute(
        select(HistorialPrecioCodigo)
        .where(HistorialPrecioCodigo.obra_social_nro == OS,
               HistorialPrecioCodigo.nomenclador_id == NOMENCLADOR_ID,
               HistorialPrecioCodigo.origen == "NN")
        .order_by(HistorialPrecioCodigo.vigencia_desde)
    )).scalars())


@pytest.mark.asyncio
async def test_lote_recalcula_una_sola_vez_el_valor_que_usa_ambos_galenos(s):
    await _sembrar(s)
    r = await actualizar_precios_galenos_lote(_body((COD_HON, "150"), (COD_GAS, "20")), s)
    assert (r.galenos, r.filas, r.valores_recalculados) == (2, 2, 1)

    assert (await _vigente(s, COD_HON)).valor_unitario == Decimal("150")
    assert (await _vigente(s, COD_GAS)).valor_unitario == Decimal("20")

    # 2 x 150 + 3 x 20 = 360; sin filas intermedias en el historial.
    h = await _historial(s)
    assert [(x.vigencia_desde, x.vigencia_hasta, x.precio_total) for x in h] == [
        (VIG, NUEVA - datetime.timedelta(days=1), Decimal("230.00")),
        (NUEVA, None, Decimal("360.00")),
    ]


@pytest.mark.asyncio
async def test_si_un_galeno_no_se_puede_actualizar_no_se_aplica_ninguno(s):
    await _sembrar(s)
    resp = await actualizar_precios_galenos_lote(_body((COD_HON, "150"), ("no_existe", "20")), s)

    assert resp.status_code == 422
    cuerpo = json.loads(resp.body)
    assert [e["codigo"] for e in cuerpo["errores"]] == ["no_existe"]
    assert "ningún cambio" in cuerpo["detail"]

    g = await _vigente(s, COD_HON)
    assert g.valor_unitario == Decimal("100") and g.vigencia_desde == VIG
    assert [x.precio_total for x in await _historial(s)] == [Decimal("230.00")]


@pytest.mark.asyncio
async def test_vigencia_anterior_a_la_del_galeno_la_reubica_y_pasa_a_ser_la_ultima(s):
    """Como en los valores: una vigencia anterior no se rechaza, es la última y reemplaza lo posterior."""
    await _sembrar(s)
    anterior = datetime.date(2026, 6, 1)
    r = await actualizar_precios_galenos_lote(
        _body((COD_HON, "150"), (COD_GAS, "20"), vigencia=anterior), s,
    )
    assert (r.galenos, r.filas, r.valores_recalculados) == (2, 2, 1)

    g = await _vigente(s, COD_HON)
    assert (g.vigencia_desde, g.valor_unitario) == (anterior, Decimal("150"))
    assert (await _vigente(s, COD_GAS)).vigencia_desde == anterior

    # El historial posterior se borró: queda una sola fila, desde la fecha cargada, con el precio nuevo.
    assert [(x.vigencia_desde, x.vigencia_hasta, x.precio_total) for x in await _historial(s)] == [
        (anterior, None, Decimal("360.00")),
    ]


@pytest.mark.asyncio
async def test_vigencia_en_el_medio_cierra_la_que_la_cubria_y_borra_las_posteriores(s):
    await _sembrar(s)
    await actualizar_precios_galenos_lote(_body((COD_HON, "150"), (COD_GAS, "20")), s)   # rota a 01/08
    medio = datetime.date(2026, 7, 15)
    await actualizar_precios_galenos_lote(_body((COD_HON, "200"), (COD_GAS, "30"), vigencia=medio), s)

    filas = list((await s.execute(
        select(Galeno).where(Galeno.obra_social_nro == OS, Galeno.codigo == COD_HON).order_by(Galeno.vigencia_desde)
    )).scalars())
    assert [(f.vigencia_desde, f.vigencia_hasta, f.valor_unitario) for f in filas] == [
        (VIG, medio - datetime.timedelta(days=1), Decimal("100")),
        (medio, None, Decimal("200")),
    ]
    # 2 x 200 + 3 x 30 = 490
    assert [(x.vigencia_desde, x.vigencia_hasta, x.precio_total) for x in await _historial(s)] == [
        (VIG, medio - datetime.timedelta(days=1), Decimal("230.00")),
        (medio, None, Decimal("490.00")),
    ]


@pytest.mark.asyncio
async def test_el_valor_sigue_apuntando_a_un_galeno_que_existe_tras_reubicar(s):
    from app.db.models.nomenclador_cmc import ValorComponente

    valor = await _sembrar(s)
    await actualizar_precios_galenos_lote(_body((COD_HON, "150"), (COD_GAS, "20")), s)
    await actualizar_precios_galenos_lote(
        _body((COD_HON, "200"), (COD_GAS, "30"), vigencia=datetime.date(2026, 6, 1)), s,
    )
    ids = {c.galeno_id for c in (await s.execute(
        select(ValorComponente).where(ValorComponente.valor_id == valor.id)
    )).scalars() if c.galeno_id}
    existentes = set((await s.execute(select(Galeno.id).where(Galeno.id.in_(ids)))).scalars())
    assert ids == existentes and len(ids) == 2


@pytest.mark.asyncio
async def test_codigo_repetido_en_el_pedido_da_422(s):
    await _sembrar(s)
    with pytest.raises(HTTPException) as e:
        await actualizar_precios_galenos_lote(_body((COD_HON, "150"), (COD_HON, "160")), s)
    assert e.value.status_code == 422


@pytest.mark.asyncio
async def test_misma_vigencia_corrige_el_precio_en_el_lugar(s):
    await _sembrar(s)
    r = await actualizar_precios_galenos_lote(_body((COD_HON, "150"), vigencia=VIG), s)
    assert r.valores_recalculados == 1
    assert (await _vigente(s, COD_HON)).valor_unitario == Decimal("150")
    h = await _historial(s)
    assert [(x.vigencia_desde, x.precio_total) for x in h] == [(VIG, Decimal("330.00"))]


@pytest.mark.asyncio
async def test_varias_variantes_del_mismo_codigo_se_recalculan_juntas(s):
    """NN y NE (sin restricción) del mismo código usan el mismo galeno: cada variante
    cierra su fila vigente y abre la nueva con el precio nuevo."""
    nn = await _sembrar(s)
    hon = await _vigente(s, COD_HON)
    await service.persistir_valor(
        s,
        Valor(obra_social_nro=OS, nomenclador_id=NOMENCLADOR_ID, origen="NE", codigo=nn.codigo,
              descripcion="NE", especialidad_id_colegio=None, sin_restriccion_especialidad=True,
              vigencia_desde=VIG, estado="activo"),
        [
            dict(concepto="Honorarios", galeno_id=hon.id, cantidad=Decimal("5"), orden=0),
            dict(concepto="Gastos", galeno_id=None, cantidad=Decimal("0"), valor_unitario=Decimal("0"), orden=1),
            dict(concepto="Ayudante", galeno_id=None, cantidad=Decimal("0"), valor_unitario=Decimal("0"), orden=2),
        ],
        motivo="carga_inicial", fecha_corte=None,
    )
    r = await actualizar_precios_galenos_lote(_body((COD_HON, "150")), s)
    assert r.valores_recalculados == 2

    filas = (await s.execute(
        select(HistorialPrecioCodigo)
        .where(HistorialPrecioCodigo.obra_social_nro == OS,
               HistorialPrecioCodigo.nomenclador_id == NOMENCLADOR_ID)
        .order_by(HistorialPrecioCodigo.origen, HistorialPrecioCodigo.vigencia_desde)
        .execution_options(populate_existing=True)
    )).scalars().all()
    assert [(f.origen, f.vigencia_desde, f.vigencia_hasta, f.precio_total) for f in filas] == [
        ("NE", VIG, NUEVA - datetime.timedelta(days=1), Decimal("500.00")),
        ("NE", NUEVA, None, Decimal("750.00")),
        ("NN", VIG, NUEVA - datetime.timedelta(days=1), Decimal("230.00")),
        ("NN", NUEVA, None, Decimal("330.00")),
    ]
