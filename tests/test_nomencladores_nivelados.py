"""Nomencladores nivelados: aplicar uno a una obra social (`nivelados.aplicar`).

Sesión sin commit (`commit` → `flush`, `rollback` → no-op): todo se descarta al
cerrar. Obra social de prueba 990_095, galeno de urología de 7 niveles creado acá.
"""
import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db.models.catalogs import Especialidad, ObrasSociales
from app.db.models.nomenclador_cmc import (
    CodigoObraSocial,
    Galeno,
    NomencladorCMC,
    NomencladorNivelado,
    NomencladorNiveladoCodigo,
    Valor,
    ValorComponente,
)
from app.modules.nomenclador import aplicar_plantilla, nivelados
from app.modules.nomenclador.routes_valores import _crear_valor_con_componentes
from app.modules.nomenclador.schemas import ValorComponenteIn

OS = 990_095
HOY = datetime.date.today()
GRUPO = "galeno_urologia"  # plantilla de 7 niveles
SLUG = "prueba_nivelado_test"


@pytest.fixture
def s(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)

    async def _no_rollback():
        return None

    monkeypatch.setattr(db, "rollback", _no_rollback)
    return db


async def _preparar(db, *, con_galenos=True):
    db.add(ObrasSociales(NRO_OBRASOCIAL=OS, OBRA_SOCIAL="PRUEBA NIVELADOS", MARCA="S", cuit="0"))
    if con_galenos:
        for nivel in range(1, 8):
            db.add(Galeno(
                obra_social_nro=OS, codigo="galeno_urologia", nombre="Galeno urologia", nivel=nivel,
                vigencia_desde=HOY, valor_unitario=Decimal("1"),
                unidades_honorarios=Decimal(nivel * 1000), unidades_ayudante=Decimal(nivel * 250),
            ))
    nom = NomencladorNivelado(slug=SLUG, nombre="Prueba", galeno_grupo=GRUPO, niveles=7)
    db.add(nom)
    await db.flush()

    codigos = list((await db.execute(
        select(NomencladorCMC).where(NomencladorCMC.activo == True).order_by(NomencladorCMC.id).limit(4)
    )).scalars())
    e1, e2 = (await db.execute(
        select(Especialidad.ID_COLEGIO_ESPE).order_by(Especialidad.ID_COLEGIO_ESPE).limit(2)
    )).scalars().all()
    a, b, c, d = codigos
    for cod in codigos:
        cod.sin_restriccion_especialidad = None
    await aplicar_plantilla.reemplazar_plantilla(db, a.codigo, [e1, e2])
    await aplicar_plantilla.reemplazar_plantilla(db, b.codigo, [e1])
    await aplicar_plantilla.reemplazar_plantilla(db, c.codigo, [])
    await aplicar_plantilla.reemplazar_plantilla(db, d.codigo, [e1])
    db.add_all([
        NomencladorNiveladoCodigo(nomenclador_nivelado_id=nom.id, nomenclador_id=a.id, nivel=3),
        NomencladorNiveladoCodigo(nomenclador_nivelado_id=nom.id, nomenclador_id=b.id, unidades=Decimal("50")),
        NomencladorNiveladoCodigo(nomenclador_nivelado_id=nom.id, nomenclador_id=c.id, nivel=2),
        NomencladorNiveladoCodigo(nomenclador_nivelado_id=nom.id, nomenclador_id=d.id, nivel=5),
    ])
    await db.flush()
    return (a, b, c, d), (e1, e2)


async def _activos(db, nom_id):
    return list((await db.execute(select(Valor).where(
        Valor.obra_social_nro == OS, Valor.nomenclador_id == nom_id, Valor.estado == "activo",
    ))).scalars())


async def _comps(db, valor_id):
    return {
        c.concepto: c for c in (await db.execute(select(ValorComponente).where(
            ValorComponente.valor_id == valor_id, ValorComponente.activo == True,
        ))).scalars()
    }


@pytest.mark.asyncio
async def test_aplicar_crea_alta_y_precio_por_especialidad_con_el_galeno_del_nivel(s):
    (a, b, c, d), (e1, e2) = await _preparar(s)
    # d ya tiene precio NE (de hoy): se reemplaza.
    await _crear_valor_con_componentes(
        db=s, obra_social_nro=OS, nomenclador_id=d.id, origen="NE", vigencia_desde=HOY,
        componentes_in=[ValorComponenteIn(concepto="Honorarios", valor_unitario=Decimal("10"))],
        descripcion="X", nivel=None, complejidad=None, especialidad_id_colegio=e1, observacion=None,
    )

    previa = await nivelados.aplicar(s, SLUG, OS, HOY, dry_run=True, usuario="t")
    estados = {f.codigo: f.estado for f in previa.filas}
    assert estados == {a.codigo: "crear", b.codigo: "crear", c.codigo: "sin_quien_factura",
                       d.codigo: "reemplazar"}
    assert await _activos(s, a.id) == []  # la vista previa no escribe
    assert next(f for f in previa.filas if f.codigo == a.codigo).precio == Decimal("3750.00")

    out = await nivelados.aplicar(s, SLUG, OS, HOY, dry_run=False, usuario="t")
    assert out.resumen.precios == 4 and out.resumen.reemplazar == 1

    va = await _activos(s, a.id)
    assert sorted(v.especialidad_id_colegio for v in va) == [e1, e2]
    assert all(v.nivel == 3 for v in va)
    comps = await _comps(s, va[0].id)
    assert comps["Honorarios"].cantidad == Decimal("3000") and comps["Ayudante"].cantidad == Decimal("750")

    # "50 unidades": galeno de nivel 1 con cantidad 50, sin ayudante.
    [vb] = await _activos(s, b.id)
    cb = await _comps(s, vb.id)
    assert vb.nivel == 1 and cb["Honorarios"].cantidad == Decimal("50") and cb["Ayudante"].cantidad == 0

    # Sin quién factura: alta sin precio.
    assert await _activos(s, c.id) == []
    par_c = (await s.execute(select(CodigoObraSocial).where(
        CodigoObraSocial.obra_social_nro == OS, CodigoObraSocial.nomenclador_id == c.id,
    ))).scalar_one()
    assert par_c.estado == "activo"

    # El que ya tenía precio con la misma fecha: se reemplazó (una sola vigencia).
    vd = list((await s.execute(select(Valor).where(
        Valor.obra_social_nro == OS, Valor.nomenclador_id == d.id,
    ))).scalars())
    assert len(vd) == 1 and vd[0].estado == "activo" and vd[0].nivel == 5

    # Reaplicar: todo lo que tiene precio se reemplaza.
    otra = await nivelados.aplicar(s, SLUG, OS, HOY, dry_run=True, usuario="t")
    assert otra.resumen.crear == 0 and otra.resumen.reemplazar == 3


@pytest.mark.asyncio
async def test_un_precio_nn_no_cuenta_como_ya_cargado(s):
    (a, *_), (e1, e2) = await _preparar(s)
    await _crear_valor_con_componentes(
        db=s, obra_social_nro=OS, nomenclador_id=a.id, origen="NN", vigencia_desde=HOY,
        componentes_in=[ValorComponenteIn(concepto="Honorarios", valor_unitario=Decimal("10"))],
        descripcion="NN", nivel=None, complejidad=None, especialidad_id_colegio=None, observacion=None,
    )

    previa = await nivelados.aplicar(s, SLUG, OS, HOY, dry_run=True, usuario="t")
    assert next(f for f in previa.filas if f.codigo == a.codigo).estado == "crear"

    await nivelados.aplicar(s, SLUG, OS, HOY, dry_run=False, usuario="t")
    va = await _activos(s, a.id)
    assert sorted((v.origen, v.especialidad_id_colegio or 0) for v in va) == [
        ("NE", e1), ("NE", e2), ("NN", 0),
    ]


async def _variante(db, nom_id, esp):
    """(vigencia_desde, vigencia_hasta, estado) de cada valor NE de la variante, y las
    filas de historial, de la más vieja a la más nueva."""
    from app.db.models.nomenclador_cmc import HistorialPrecioCodigo as H

    valores = [(v.vigencia_desde, v.vigencia_hasta, v.estado) for v in (await db.execute(
        select(Valor).where(Valor.obra_social_nro == OS, Valor.nomenclador_id == nom_id,
                            Valor.origen == "NE", Valor.especialidad_id_colegio == esp)
        .order_by(Valor.vigencia_desde)
    )).scalars()]
    hist = [(h.vigencia_desde, h.vigencia_hasta) for h in (await db.execute(
        select(H).where(H.obra_social_nro == OS, H.nomenclador_id == nom_id,
                        H.origen == "NE", H.especialidad_id_colegio == esp)
        .order_by(H.vigencia_desde)
    )).scalars()]
    return valores, hist


@pytest.mark.asyncio
async def test_vigencia_anterior_borra_las_mas_nuevas_y_queda_como_ultima(s):
    (_, b, *_), (e1, _) = await _preparar(s)
    d1, d2, d3 = HOY - datetime.timedelta(days=60), HOY - datetime.timedelta(days=30), HOY
    for f in (d1, d3):
        await nivelados.aplicar(s, SLUG, OS, f, dry_run=False, usuario="t")
    assert [v[0] for v in (await _variante(s, b.id, e1))[0]] == [d1, d3]

    previa = await nivelados.aplicar(s, SLUG, OS, d2, dry_run=True, usuario="t")
    fb = next(f for f in previa.filas if f.codigo == b.codigo)
    assert fb.estado == "reemplazar" and fb.vigencias_borradas == 1

    await nivelados.aplicar(s, SLUG, OS, d2, dry_run=False, usuario="t")
    valores, hist = await _variante(s, b.id, e1)
    corte = d2 - datetime.timedelta(days=1)
    assert valores == [(d1, corte, "cerrado"), (d2, None, "activo")]
    assert hist == [(d1, corte), (d2, None)]


@pytest.mark.asyncio
async def test_misma_vigencia_reemplaza_sin_rotar(s):
    (_, b, *_), (e1, _) = await _preparar(s)
    d1 = HOY - datetime.timedelta(days=30)
    await nivelados.aplicar(s, SLUG, OS, d1, dry_run=False, usuario="t")
    await nivelados.aplicar(s, SLUG, OS, HOY, dry_run=False, usuario="t")
    await nivelados.aplicar(s, SLUG, OS, HOY, dry_run=False, usuario="t")
    valores, hist = await _variante(s, b.id, e1)
    corte = HOY - datetime.timedelta(days=1)
    assert valores == [(d1, corte, "cerrado"), (HOY, None, "activo")]
    assert hist == [(d1, corte), (HOY, None)]


@pytest.mark.asyncio
async def test_sin_los_galenos_del_nivel_da_409(s):
    await _preparar(s, con_galenos=False)
    with pytest.raises(HTTPException) as exc:
        await nivelados.aplicar(s, SLUG, OS, HOY, dry_run=True, usuario="t")
    assert exc.value.status_code == 409 and exc.value.detail["faltan"] == [1, 2, 3, 5]


@pytest.mark.asyncio
async def test_editar_y_agregar_codigo_validan_el_nivel(s):
    from app.modules.nomenclador.schemas import NiveladoCodigoAltaIn, NiveladoCodigoIn

    (a, *_), _ = await _preparar(s)
    with pytest.raises(HTTPException):
        await nivelados.actualizar_codigo(s, SLUG, a.id, NiveladoCodigoIn(nivel=9))
    out = await nivelados.actualizar_codigo(s, SLUG, a.id, NiveladoCodigoIn(nivel=6))
    assert out.nivel == 6
    with pytest.raises(HTTPException) as exc:
        await nivelados.agregar_codigo(s, SLUG, NiveladoCodigoAltaIn(nomenclador_id=a.id, nivel=1))
    assert exc.value.status_code == 409
    with pytest.raises(ValueError):
        NiveladoCodigoIn(nivel=2, unidades=Decimal("5"))
