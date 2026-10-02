"""Aumento porcentual de una OS (valores fijos y galenos) y su reversión.

Datos sembrados en la sesión y NUNCA commiteados: `commit` → `flush`, `rollback`
→ no-op; el fixture `db` cierra la sesión sin commit. OS inexistentes.
"""
import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models.nomenclador_cmc import Galeno, HistorialPrecioCodigo, NomencladorCMC, Valor
from app.modules.nomenclador import aumento, service
from app.modules.nomenclador.schemas import ActualizarPorcentajeIn, RevertirActualizacionIn

OS = 990_041
NOMENCLADOR_ID = 897
VIGENCIA = datetime.date(2026, 7, 1)
NUEVA = datetime.date(2026, 8, 1)


@pytest.fixture
def s(db, monkeypatch):
    async def _noop():
        return None

    monkeypatch.setattr(db, "commit", db.flush)
    monkeypatch.setattr(db, "rollback", _noop)
    return db


async def _fijo(db, honorarios="24000.00", *, por_presupuesto=False, origen="NE") -> Valor:
    nom = await db.get(NomencladorCMC, NOMENCLADOR_ID)
    v = Valor(
        obra_social_nro=OS, nomenclador_id=NOMENCLADOR_ID, origen=origen, codigo=nom.codigo,
        descripcion="PRUEBA", especialidad_id_colegio=None,
        sin_restriccion_especialidad=(origen == "NE"),
        por_presupuesto=por_presupuesto, vigencia_desde=VIGENCIA, estado="activo",
    )
    vu = Decimal("0") if por_presupuesto else Decimal(honorarios)
    return await service.persistir_valor(
        db, v,
        [
            dict(concepto="Honorarios", galeno_id=None, cantidad=Decimal("0"), valor_unitario=vu, orden=0),
            dict(concepto="Gastos", galeno_id=None, cantidad=Decimal("0"), valor_unitario=Decimal("0"), orden=1),
            dict(concepto="Ayudante", galeno_id=None, cantidad=Decimal("0"), valor_unitario=Decimal("0"), orden=2),
        ],
        motivo="carga_inicial", fecha_corte=None,
    )


async def _activos(db, origen="NE"):
    return list((await db.execute(select(Valor).where(
        Valor.obra_social_nro == OS, Valor.nomenclador_id == NOMENCLADOR_ID,
        Valor.origen == origen, Valor.estado == "activo",
    ))).scalars())


def _total(v: Valor) -> Decimal:
    return sum((c.subtotal for c in v.componentes if c.activo), Decimal("0"))


def _body(**kw) -> ActualizarPorcentajeIn:
    base = dict(obra_social_nro=OS, origen="NE", porcentaje=Decimal("10"), vigencia_desde=NUEVA)
    base.update(kw)
    return ActualizarPorcentajeIn(**base)


def test_rango_numerico():
    assert not aumento._en_rango("1500", "100000", "199999")
    assert aumento._en_rango("150000", "100000", "199999")


@pytest.mark.asyncio
async def test_aumento_fijo_y_no_acumula_con_la_misma_vigencia(s):
    await _fijo(s)

    r = await aumento.aplicar_aumento(s, _body())
    assert r.actualizados == 1
    [v] = await _activos(s)
    assert v.vigencia_desde == NUEVA and _total(v) == Decimal("26400.00")

    r2 = await aumento.aplicar_aumento(s, _body())
    assert r2.actualizados == 0 and r2.omitidos == 1
    assert "vigencia igual o posterior" in r2.detalle[0].motivo
    [v] = await _activos(s)
    assert _total(v) == Decimal("26400.00")


@pytest.mark.asyncio
async def test_vigencia_vieja_y_presupuesto_se_omiten(s):
    await _fijo(s, por_presupuesto=True)
    r = await aumento.aplicar_aumento(s, _body())
    assert r.actualizados == 0 and r.omitidos == 1
    assert "presupuesto" in r.detalle[0].motivo.lower()


@pytest.mark.asyncio
async def test_vigencia_anterior_a_la_actual_se_omite(s):
    await _fijo(s)
    r = await aumento.aplicar_aumento(s, _body(vigencia_desde=datetime.date(2026, 3, 1)))
    assert r.actualizados == 0 and r.omitidos == 1
    [v] = await _activos(s)
    assert v.vigencia_desde == VIGENCIA and v.vigencia_hasta is None


@pytest.mark.asyncio
async def test_dry_run_no_escribe(s):
    await _fijo(s)
    r = await aumento.aplicar_aumento(s, _body(dry_run=True))
    assert r.dry_run and r.actualizados == 1
    assert r.detalle[0].nuevo == Decimal("26400.00")
    [v] = await _activos(s)
    assert v.vigencia_desde == VIGENCIA


async def _nn_por_galeno(db, vu="1000.00") -> tuple[Galeno, Valor]:
    g = Galeno(
        obra_social_nro=OS, codigo="galeno_prueba_aumento", nombre="Galeno prueba",
        nivel=None, vigencia_desde=VIGENCIA, valor_unitario=Decimal(vu), activo=True,
    )
    db.add(g)
    await db.flush()
    nom = await db.get(NomencladorCMC, NOMENCLADOR_ID)
    v = await service.persistir_valor(
        db,
        Valor(obra_social_nro=OS, nomenclador_id=NOMENCLADOR_ID, origen="NN", codigo=nom.codigo,
              descripcion="NN", vigencia_desde=VIGENCIA, estado="activo"),
        [
            dict(concepto="Honorarios", galeno_id=g.id, cantidad=Decimal("2"), orden=0),
            dict(concepto="Gastos", galeno_id=g.id, cantidad=Decimal("0"), orden=1),
            dict(concepto="Ayudante", galeno_id=g.id, cantidad=Decimal("0"), orden=2),
        ],
        motivo="carga_inicial", fecha_corte=None,
    )
    return g, v


async def _precio_historial(db, fecha) -> Decimal:
    fila = (await db.execute(select(HistorialPrecioCodigo).where(
        HistorialPrecioCodigo.obra_social_nro == OS,
        HistorialPrecioCodigo.nomenclador_id == NOMENCLADOR_ID,
        HistorialPrecioCodigo.origen == "NN",
        HistorialPrecioCodigo.vigencia_desde <= fecha,
        (HistorialPrecioCodigo.vigencia_hasta.is_(None)) | (HistorialPrecioCodigo.vigencia_hasta >= fecha),
    ))).scalars().first()
    return fila.precio_total


@pytest.mark.asyncio
async def test_aumento_de_galeno_actualiza_el_nn_y_se_revierte(s):
    await _nn_por_galeno(s)
    assert await _precio_historial(s, NUEVA) == Decimal("2000.00")

    r = await aumento.aplicar_aumento(s, _body(
        origen="NN", incluir_valores_fijos=False, galeno_codigos=["galeno_prueba_aumento"],
    ))
    assert r.galenos_actualizados == 1
    assert await _precio_historial(s, NUEVA) == Decimal("2200.00")
    assert await _precio_historial(s, VIGENCIA) == Decimal("2000.00")

    # Misma vigencia otra vez: no acumula.
    r2 = await aumento.aplicar_aumento(s, _body(
        origen="NN", incluir_valores_fijos=False, galeno_codigos=["galeno_prueba_aumento"],
    ))
    assert r2.galenos_actualizados == 0 and r2.omitidos == 1
    assert await _precio_historial(s, NUEVA) == Decimal("2200.00")

    rev = await aumento.revertir_aumento(
        s, RevertirActualizacionIn(obra_social_nro=OS, vigencia_revertir=NUEVA)
    )
    assert rev.galenos_actualizados == 1
    assert await _precio_historial(s, NUEVA) == Decimal("2000.00")


@pytest.mark.asyncio
async def test_revertir_fijo_vuelve_al_anterior_sin_fechas_invertidas(s):
    original = await _fijo(s)
    await aumento.aplicar_aumento(s, _body())

    prev = await aumento.revertir_aumento(
        s, RevertirActualizacionIn(obra_social_nro=OS, vigencia_revertir=NUEVA, dry_run=True)
    )
    assert prev.actualizados == 1 and prev.detalle[0].nuevo == Decimal("24000.00")

    rev = await aumento.revertir_aumento(
        s, RevertirActualizacionIn(obra_social_nro=OS, vigencia_revertir=NUEVA)
    )
    assert rev.actualizados == 1
    [v] = await _activos(s)
    assert v.id == original.id and _total(v) == Decimal("24000.00")
    revertido = (await s.execute(select(Valor).where(
        Valor.obra_social_nro == OS, Valor.vigencia_desde == NUEVA
    ))).scalars().one()
    assert revertido.estado == "cerrado" and revertido.vigencia_hasta >= revertido.vigencia_desde


@pytest.mark.asyncio
async def test_revertir_no_toca_altas_de_ese_dia(s):
    nom = await db_get_nom(s)
    alta = Valor(
        obra_social_nro=OS, nomenclador_id=NOMENCLADOR_ID, origen="NE", codigo=nom.codigo,
        descripcion="ALTA", especialidad_id_colegio=None, sin_restriccion_especialidad=True,
        vigencia_desde=NUEVA, estado="activo",
    )
    await service.persistir_valor(
        s, alta,
        [dict(concepto="Honorarios", galeno_id=None, cantidad=Decimal("0"),
              valor_unitario=Decimal("5000"), orden=0)],
        motivo="carga_inicial", fecha_corte=None,
    )
    rev = await aumento.revertir_aumento(
        s, RevertirActualizacionIn(obra_social_nro=OS, vigencia_revertir=NUEVA)
    )
    assert rev.actualizados == 0
    [v] = await _activos(s)
    assert v.estado == "activo"


async def db_get_nom(db):
    return await db.get(NomencladorCMC, NOMENCLADOR_ID)
