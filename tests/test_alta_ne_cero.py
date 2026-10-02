"""Herramienta "Agregar código a obras sociales" (`aplicar_plantilla.alta_ne_en_cero`).

Da de alta el código como NE en $0, una variante por especialidad de la plantilla
(o una sin especialidad si el código es "sin restricción"), y NO toca lo que ya
existe. Sesión sin commit (`commit` → `flush`).
"""
import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models.catalogs import Especialidad, ObrasSociales
from app.db.models.nomenclador_cmc import (
    HistorialPrecioCodigo,
    NomencladorCMC,
    NomencladorPlantillaEspecialidad,
    Valor,
    ValorEspecialidad,
)
from app.modules.nomenclador.aplicar_plantilla import alta_ne_en_cero

OS_A, OS_B = 990_091, 990_092
VIG = datetime.date(2026, 1, 1)


@pytest.fixture
def s(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)
    return db


async def _setup(db, *, sin_restriccion=False, plantilla=True) -> tuple[NomencladorCMC, list[int]]:
    for nro in (OS_A, OS_B):
        db.add(ObrasSociales(NRO_OBRASOCIAL=nro, OBRA_SOCIAL=f"PRUEBA ALTA NE {nro}", MARCA="S", cuit="0"))
    nom = NomencladorCMC(codigo="ZZ9901", descripcion="PRUEBA ALTA NE", activo=True,
                         sin_restriccion_especialidad=sin_restriccion)
    db.add(nom)
    esps = list((await db.execute(
        select(Especialidad.ID_COLEGIO_ESPE).order_by(Especialidad.ID_COLEGIO_ESPE).limit(2)
    )).scalars())
    if plantilla:
        for e in esps:
            db.add(NomencladorPlantillaEspecialidad(codigo="ZZ9901", especialidad_id_colegio=e))
    await db.flush()
    return nom, esps


async def _ne(db, os_nro) -> list[Valor]:
    return list((await db.execute(select(Valor).where(
        Valor.obra_social_nro == os_nro, Valor.codigo == "ZZ9901",
        Valor.origen == "NE", Valor.estado == "activo",
    ))).scalars())


@pytest.mark.asyncio
async def test_crea_un_ne_en_cero_por_especialidad_con_historial(s):
    nom, esps = await _setup(s)
    r = await alta_ne_en_cero(s, nom, [OS_A, OS_B], VIG, None)

    assert r["plantilla"] == esps
    assert [o["estado"] for o in r["resultados"]] == ["creada", "creada"]
    for os_nro in (OS_A, OS_B):
        ne = await _ne(s, os_nro)
        assert sorted(v.especialidad_id_colegio for v in ne) == esps
        assert all(v.vigencia_desde == VIG and not v.sin_restriccion_especialidad for v in ne)
        hist = (await s.execute(select(HistorialPrecioCodigo).where(
            HistorialPrecioCodigo.valores_id.in_([v.id for v in ne])
        ))).scalars().all()
        assert len(hist) == len(ne) and all(h.precio_total == Decimal("0") for h in hist)
        habilitadas = set((await s.execute(select(ValorEspecialidad.especialidad_id_colegio).where(
            ValorEspecialidad.obra_social_nro == os_nro, ValorEspecialidad.codigo == "ZZ9901",
        ))).scalars())
        assert habilitadas == set(esps)


@pytest.mark.asyncio
async def test_no_toca_lo_existente(s):
    nom, esps = await _setup(s)
    # OS_A ya tiene la primera especialidad cargada.
    await alta_ne_en_cero(s, nom, [OS_A], VIG, None)
    previa = {v.especialidad_id_colegio: v.id for v in await _ne(s, OS_A)}

    r = await alta_ne_en_cero(s, nom, [OS_A], datetime.date(2026, 5, 1), None)
    assert r["resultados"][0]["estado"] == "sin_cambios"
    assert sorted(r["resultados"][0]["existentes"]) == esps
    ne = await _ne(s, OS_A)
    assert {v.especialidad_id_colegio: v.id for v in ne} == previa  # nada cerrado ni recreado
    assert all(v.vigencia_desde == VIG for v in ne)


@pytest.mark.asyncio
async def test_sin_restriccion_crea_uno_sin_especialidad_y_omite_par_por_especialidad(s):
    nom, esps = await _setup(s)
    # OS_B ya tiene el código por especialidad (antes de marcarlo sin restricción).
    await alta_ne_en_cero(s, nom, [OS_B], VIG, None)
    nom.sin_restriccion_especialidad = True
    await s.flush()

    r = await alta_ne_en_cero(s, nom, [OS_A, OS_B], VIG, None)
    por_os = {o["obra_social_nro"]: o for o in r["resultados"]}
    assert por_os[OS_A]["estado"] == "creada" and por_os[OS_A]["creadas"] == [None]
    ne_a = await _ne(s, OS_A)
    assert len(ne_a) == 1 and ne_a[0].especialidad_id_colegio is None
    assert ne_a[0].sin_restriccion_especialidad
    assert por_os[OS_B]["estado"] == "omitida"
    assert sorted(v.especialidad_id_colegio for v in await _ne(s, OS_B)) == esps


@pytest.mark.asyncio
async def test_descripcion_del_catalogo_o_la_que_ya_tiene_la_os(s):
    nom, esps = await _setup(s)
    await alta_ne_en_cero(s, nom, [OS_A], VIG, None)
    assert {v.descripcion for v in await _ne(s, OS_A)} == {"PRUEBA ALTA NE"}  # la del catálogo

    # La OS le puso su propia descripción; una especialidad nueva en la plantilla la hereda.
    for v in await _ne(s, OS_A):
        v.descripcion = "PROPIA DE LA OS"
    otra = (await s.execute(
        select(Especialidad.ID_COLEGIO_ESPE).where(Especialidad.ID_COLEGIO_ESPE.not_in(esps))
        .order_by(Especialidad.ID_COLEGIO_ESPE).limit(1)
    )).scalar_one()
    s.add(NomencladorPlantillaEspecialidad(codigo="ZZ9901", especialidad_id_colegio=otra))
    await s.flush()

    r = await alta_ne_en_cero(s, nom, [OS_A], VIG, None)
    assert r["resultados"][0]["creadas"] == [otra]
    assert {v.descripcion for v in await _ne(s, OS_A)} == {"PROPIA DE LA OS"}


@pytest.mark.asyncio
async def test_sin_plantilla_falla(s):
    nom, _ = await _setup(s, plantilla=False)
    with pytest.raises(ValueError, match="plantilla"):
        await alta_ne_en_cero(s, nom, [OS_A], VIG, None)
