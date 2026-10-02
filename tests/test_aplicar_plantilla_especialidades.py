"""Plantilla de especialidades de un código y su aplicación a obras sociales.

Los datos se siembran en la sesión con `flush` y NUNCA se commitean. Como
`aplicar_a_obras_sociales` commitea por obra social, cada test reemplaza
`db.commit` por `db.flush` y `db.rollback` por un no-op: el fixture `db` cierra la
sesión sin commit y MySQL descarta todo. Las obras sociales son números que no
existen, para no cruzarse con variantes reales.
"""
import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db.models.catalogs import Especialidad
from app.db.models.nomenclador_cmc import NomencladorCMC, Valor, ValorEspecialidad
from app.modules.nomenclador import aplicar_plantilla, service
from app.modules.nomenclador.routes_nomenclador import aplicar_especialidades
from app.modules.nomenclador.schemas import AplicarEspecialidadesIn

VIGENCIA = datetime.date(2026, 7, 1)
NOMENCLADOR_ID = 897  # 030801 — cualquier código real sirve
OS_CON_CODIGO = 990_011
OS_SIN_CODIGO = 990_012


@pytest.fixture
def sin_commit(db, monkeypatch):
    async def _noop():
        return None

    monkeypatch.setattr(db, "commit", db.flush)
    monkeypatch.setattr(db, "rollback", _noop)
    return db


async def _dos_especialidades(db) -> tuple[int, int]:
    ids = (await db.execute(
        select(Especialidad.ID_COLEGIO_ESPE).distinct().limit(2)
    )).scalars().all()
    assert len(ids) == 2
    return ids[0], ids[1]


async def _sembrar_base(db, especialidad_id, honorarios="24000.00") -> Valor:
    nom = await db.get(NomencladorCMC, NOMENCLADOR_ID)
    await service.validar_especialidad_habilitada(db, nom.codigo, OS_CON_CODIGO, especialidad_id)
    valor = Valor(
        obra_social_nro=OS_CON_CODIGO, nomenclador_id=NOMENCLADOR_ID, origen="NE",
        codigo=nom.codigo, descripcion="PRUEBA PLANTILLA",
        especialidad_id_colegio=especialidad_id, vigencia_desde=VIGENCIA, estado="activo",
    )
    return await service.persistir_valor(
        db, valor,
        [
            dict(concepto="Honorarios", galeno_id=None, cantidad=Decimal("0"),
                 valor_unitario=Decimal(honorarios), orden=0),
            dict(concepto="Gastos", galeno_id=None, cantidad=Decimal("0"),
                 valor_unitario=Decimal("0"), orden=1),
            dict(concepto="Ayudante", galeno_id=None, cantidad=Decimal("0"),
                 valor_unitario=Decimal("0"), orden=2),
        ],
        motivo="carga_inicial", fecha_corte=None,
    )


async def _activos(db, os_nro) -> list[Valor]:
    return list((await db.execute(
        select(Valor).where(
            Valor.obra_social_nro == os_nro,
            Valor.nomenclador_id == NOMENCLADOR_ID,
            Valor.estado == "activo",
        )
    )).scalars())


@pytest.mark.asyncio
async def test_reemplazar_plantilla_es_reemplazo_total_y_rechaza_inexistentes(sin_commit):
    db = sin_commit
    nom = await db.get(NomencladorCMC, NOMENCLADOR_ID)
    e1, e2 = await _dos_especialidades(db)

    await aplicar_plantilla.reemplazar_plantilla(db, nom.codigo, [e1, e2, e1])
    assert sorted(await aplicar_plantilla.leer_plantilla(db, nom.codigo)) == sorted({e1, e2})

    await aplicar_plantilla.reemplazar_plantilla(db, nom.codigo, [e2])
    assert await aplicar_plantilla.leer_plantilla(db, nom.codigo) == [e2]

    with pytest.raises(ValueError):
        await aplicar_plantilla.reemplazar_plantilla(db, nom.codigo, [-999])


@pytest.mark.asyncio
async def test_aplicar_crea_ne_con_precio_y_vigencia_de_la_base_y_omite_os_sin_codigo(sin_commit):
    db = sin_commit
    nom = await db.get(NomencladorCMC, NOMENCLADOR_ID)
    nom.sin_restriccion_especialidad = None
    e1, e2 = await _dos_especialidades(db)
    await _sembrar_base(db, e1)
    await aplicar_plantilla.reemplazar_plantilla(db, nom.codigo, [e1, e2])

    r = await aplicar_plantilla.aplicar_a_obras_sociales(db, nom, [OS_CON_CODIGO, OS_SIN_CODIGO])

    assert [(o.obra_social_nro, o.motivo) for o in r.omitidas] == [
        (OS_SIN_CODIGO, aplicar_plantilla.MOTIVO_SIN_CODIGO)
    ]
    assert len(r.aplicadas) == 1
    ap = r.aplicadas[0]
    assert (ap.obra_social_nro, ap.variantes_creadas, ap.variantes_existentes) == (
        OS_CON_CODIGO, 1, 1
    )

    activos = await _activos(db, OS_CON_CODIGO)
    assert {v.especialidad_id_colegio for v in activos} == {e1, e2}
    nueva = next(v for v in activos if v.especialidad_id_colegio == e2)
    assert nueva.origen == "NE"
    assert nueva.vigencia_desde == VIGENCIA
    assert sum(c.subtotal for c in nueva.componentes) == Decimal("24000.00")

    habilitadas = await service.especialidades_habilitadas_de(db, nom.codigo, OS_CON_CODIGO)
    assert {e1, e2} <= habilitadas


@pytest.mark.asyncio
async def test_aplicar_es_idempotente(sin_commit):
    db = sin_commit
    nom = await db.get(NomencladorCMC, NOMENCLADOR_ID)
    nom.sin_restriccion_especialidad = None
    e1, e2 = await _dos_especialidades(db)
    await _sembrar_base(db, e1)
    await aplicar_plantilla.reemplazar_plantilla(db, nom.codigo, [e1, e2])

    await aplicar_plantilla.aplicar_a_obras_sociales(db, nom, [OS_CON_CODIGO])
    r = await aplicar_plantilla.aplicar_a_obras_sociales(db, nom, [OS_CON_CODIGO])

    assert r.aplicadas[0].variantes_creadas == 0
    assert r.aplicadas[0].variantes_existentes == 2
    assert len(await _activos(db, OS_CON_CODIGO)) == 2


@pytest.mark.asyncio
async def test_aplicar_sin_restriccion_fija_el_flag_y_no_crea_variantes(sin_commit):
    db = sin_commit
    nom = await db.get(NomencladorCMC, NOMENCLADOR_ID)
    e1, _ = await _dos_especialidades(db)
    await _sembrar_base(db, e1)
    nom.sin_restriccion_especialidad = True

    r = await aplicar_plantilla.aplicar_a_obras_sociales(db, nom, [OS_CON_CODIGO])

    assert r.aplicadas[0].variantes_creadas == 0
    assert await service.par_sin_restriccion(db, nom.codigo, OS_CON_CODIGO)
    assert len(await _activos(db, OS_CON_CODIGO)) == 1


@pytest.mark.asyncio
async def test_endpoint_sin_plantilla_ni_sin_restriccion_da_422(sin_commit):
    db = sin_commit
    nom = await db.get(NomencladorCMC, NOMENCLADOR_ID)
    nom.sin_restriccion_especialidad = None
    await aplicar_plantilla.reemplazar_plantilla(db, nom.codigo, [])

    with pytest.raises(HTTPException) as e:
        await aplicar_especialidades(
            NOMENCLADOR_ID, AplicarEspecialidadesIn(obra_social_nros=[OS_CON_CODIGO]), db
        )
    assert e.value.status_code == 422


@pytest.mark.asyncio
async def test_valor_especialidad_no_se_ensucia_en_os_omitida(sin_commit):
    db = sin_commit
    nom = await db.get(NomencladorCMC, NOMENCLADOR_ID)
    nom.sin_restriccion_especialidad = None
    e1, _ = await _dos_especialidades(db)
    await aplicar_plantilla.reemplazar_plantilla(db, nom.codigo, [e1])

    await aplicar_plantilla.aplicar_a_obras_sociales(db, nom, [OS_SIN_CODIGO])

    filas = (await db.execute(
        select(ValorEspecialidad).where(ValorEspecialidad.obra_social_nro == OS_SIN_CODIGO)
    )).scalars().all()
    assert filas == []


# ─── Núcleo: editar un código PARA TODAS sus especialidades ──────────────────

from app.modules.nomenclador import nucleo  # noqa: E402
from app.modules.nomenclador.schemas import (  # noqa: E402
    ValorCerrarYCrearIn,
    ValorComponenteIn,
    ValorNucleoUpdate,
)

MEDICO_SIN_ESPECIALIDAD = 2358
FECHA_LOOKUP = datetime.date(2026, 9, 1)


async def _tres_especialidades(db) -> tuple[int, int, int]:
    ids = (await db.execute(
        select(Especialidad.ID_COLEGIO_ESPE).distinct().limit(3)
    )).scalars().all()
    assert len(ids) == 3
    return ids[0], ids[1], ids[2]


async def _sembrar_par(db, *esps) -> NomencladorCMC:
    nom = await db.get(NomencladorCMC, NOMENCLADOR_ID)
    nom.sin_restriccion_especialidad = None
    for e in esps:
        await _sembrar_base(db, e) if e is esps[0] else await _clonar_ne(db, e)
    return nom


async def _clonar_ne(db, esp):
    from app.modules.nomenclador.routes_valores import _clonar_valor

    base = (await _activos(db, OS_CON_CODIGO))[0]
    await service.validar_especialidad_habilitada(db, base.codigo, OS_CON_CODIGO, esp)
    return await _clonar_valor(
        db, base, base.vigencia_desde, motivo="replicacion", como_ne_de_especialidad=esp
    )


def _esps(valores):
    return {v.especialidad_id_colegio for v in valores}


@pytest.mark.asyncio
async def test_nucleo_agrega_y_quita_especialidades(sin_commit):
    db = sin_commit
    e1, e2, e3 = await _tres_especialidades(db)
    await _sembrar_par(db, e1, e2)

    r = await nucleo.actualizar_nucleo(
        db, OS_CON_CODIGO, NOMENCLADOR_ID,
        ValorNucleoUpdate(sin_restriccion_especialidad=False, especialidades=[e1, e3]),
    )

    assert _esps(r) == {e1, e3}
    nueva = next(v for v in r if v.especialidad_id_colegio == e3)
    assert nueva.vigencia_desde == VIGENCIA
    assert sum(c.subtotal for c in nueva.componentes) == Decimal("24000.00")
    nom = await db.get(NomencladorCMC, NOMENCLADOR_ID)
    assert await service.especialidades_habilitadas_de(db, nom.codigo, OS_CON_CODIGO) == {e1, e3}


@pytest.mark.asyncio
async def test_nucleo_sin_restriccion_deja_una_sola_fila_y_cotiza_para_cualquiera(sin_commit):
    db = sin_commit
    e1, e2, _ = await _tres_especialidades(db)
    nom = await _sembrar_par(db, e1, e2)

    r = await nucleo.actualizar_nucleo(
        db, OS_CON_CODIGO, NOMENCLADOR_ID,
        ValorNucleoUpdate(sin_restriccion_especialidad=True),
    )

    assert len(r) == 1
    assert r[0].especialidad_id_colegio is None
    assert r[0].sin_restriccion_especialidad is True
    assert await service.especialidades_habilitadas_de(db, nom.codigo, OS_CON_CODIGO) == set()
    precio = await service.lookup_precio(
        nomenclador_id=NOMENCLADOR_ID, obra_social_nro=OS_CON_CODIGO, fecha=FECHA_LOOKUP,
        medico_id=MEDICO_SIN_ESPECIALIDAD, db=db,
    )
    assert precio.precio_total == 24_000


@pytest.mark.asyncio
async def test_nucleo_de_sin_restriccion_a_especialidades(sin_commit):
    db = sin_commit
    e1, e2, _ = await _tres_especialidades(db)
    await _sembrar_par(db, e1, e2)
    await nucleo.actualizar_nucleo(
        db, OS_CON_CODIGO, NOMENCLADOR_ID, ValorNucleoUpdate(sin_restriccion_especialidad=True)
    )

    r = await nucleo.actualizar_nucleo(
        db, OS_CON_CODIGO, NOMENCLADOR_ID,
        ValorNucleoUpdate(sin_restriccion_especialidad=False, especialidades=[e1, e2]),
    )

    assert _esps(r) == {e1, e2}
    assert all(v.sin_restriccion_especialidad is False for v in r)


@pytest.mark.asyncio
async def test_nucleo_sin_especialidades_ni_sin_restriccion_da_422(sin_commit):
    db = sin_commit
    e1, _, _ = await _tres_especialidades(db)
    await _sembrar_par(db, e1)

    with pytest.raises(HTTPException) as e:
        await nucleo.actualizar_nucleo(
            db, OS_CON_CODIGO, NOMENCLADOR_ID,
            ValorNucleoUpdate(sin_restriccion_especialidad=False, especialidades=[]),
        )
    assert e.value.status_code == 422


@pytest.mark.asyncio
async def test_nucleo_ecuacion_y_metadatos_van_a_todas_las_variantes(sin_commit):
    db = sin_commit
    e1, e2, _ = await _tres_especialidades(db)
    await _sembrar_par(db, e1, e2)

    r = await nucleo.actualizar_nucleo(
        db, OS_CON_CODIGO, NOMENCLADOR_ID,
        ValorNucleoUpdate(
            descripcion="NUEVA DESC", nivel=3,
            ecuacion=ValorCerrarYCrearIn(
                vigencia_desde=datetime.date(2026, 8, 1),
                componentes=[ValorComponenteIn(
                    concepto="Honorarios", valor_unitario=Decimal("30000"))],
            ),
            sin_restriccion_especialidad=False, especialidades=[e1, e2],
        ),
    )

    assert _esps(r) == {e1, e2}
    for v in r:
        assert v.descripcion == "NUEVA DESC"
        assert v.nivel == 3
        assert v.vigencia_desde == datetime.date(2026, 8, 1)
        assert sum(c.subtotal for c in v.componentes) == Decimal("30000")


# ─── Núcleo de un código que sólo tiene fila NN ──────────────────────────────

async def _sembrar_nn(db) -> Valor:
    nom = await db.get(NomencladorCMC, NOMENCLADOR_ID)
    valor = Valor(
        obra_social_nro=OS_CON_CODIGO, nomenclador_id=NOMENCLADOR_ID, origen="NN",
        codigo=nom.codigo, descripcion="PRUEBA NN", especialidad_id_colegio=None,
        vigencia_desde=VIGENCIA, estado="activo",
    )
    return await service.persistir_valor(
        db, valor,
        [
            dict(concepto="Honorarios", galeno_id=None, cantidad=Decimal("0"),
                 valor_unitario=Decimal("10000.00"), orden=0),
            dict(concepto="Gastos", galeno_id=None, cantidad=Decimal("0"),
                 valor_unitario=Decimal("0"), orden=1),
            dict(concepto="Ayudante", galeno_id=None, cantidad=Decimal("0"),
                 valor_unitario=Decimal("0"), orden=2),
        ],
        motivo="carga_inicial", fecha_corte=None,
    )


@pytest.mark.asyncio
async def test_nucleo_nn_maneja_especialidades_sin_crear_filas(sin_commit):
    db = sin_commit
    e1, e2, _ = await _tres_especialidades(db)
    await _sembrar_nn(db)
    nom = await db.get(NomencladorCMC, NOMENCLADOR_ID)

    r = await nucleo.actualizar_nucleo(
        db, OS_CON_CODIGO, NOMENCLADOR_ID,
        ValorNucleoUpdate(descripcion="DESC NN", sin_restriccion_especialidad=False,
                          especialidades=[e1, e2]),
    )
    assert len(r) == 1 and r[0].origen == "NN"
    assert r[0].descripcion == "DESC NN"
    assert await service.especialidades_habilitadas_de(db, nom.codigo, OS_CON_CODIGO) == {e1, e2}

    r = await nucleo.actualizar_nucleo(
        db, OS_CON_CODIGO, NOMENCLADOR_ID,
        ValorNucleoUpdate(sin_restriccion_especialidad=True),
    )
    assert len(r) == 1 and r[0].sin_restriccion_especialidad is True
    assert await service.especialidades_habilitadas_de(db, nom.codigo, OS_CON_CODIGO) == set()

    with pytest.raises(HTTPException) as e:
        await nucleo.actualizar_nucleo(
            db, OS_CON_CODIGO, NOMENCLADOR_ID,
            ValorNucleoUpdate(sin_restriccion_especialidad=False, especialidades=[]),
        )
    assert e.value.status_code == 422


@pytest.mark.asyncio
async def test_nucleo_nn_con_ne_presente_solo_toca_la_fila_nn(sin_commit):
    db = sin_commit
    e1, e2, _ = await _tres_especialidades(db)
    await _sembrar_par(db, e1, e2)
    await _sembrar_nn(db)

    r = await nucleo.actualizar_nucleo(
        db, OS_CON_CODIGO, NOMENCLADOR_ID,
        ValorNucleoUpdate(
            origen="NN", observacion="OBS NN",
            sin_restriccion_especialidad=False, especialidades=[e1, e2],
        ),
    )

    nn = [v for v in r if v.origen == "NN"]
    ne = [v for v in r if v.origen == "NE"]
    assert len(nn) == 1 and nn[0].observacion == "OBS NN"
    assert _esps(ne) == {e1, e2}
    assert all(v.observacion != "OBS NN" for v in ne)


# ─── Generación NN: siembra el paso 1 (quién puede cobrar) desde el catálogo ──

@pytest.mark.asyncio
async def test_generar_nn_siembra_habilitacion_desde_plantilla_sin_pisar_lo_configurado(sin_commit):
    db = sin_commit
    os_nueva = 990_021
    await service.crear_galenos_base(os_nueva, VIGENCIA, db)

    nom = await db.get(NomencladorCMC, NOMENCLADOR_ID)
    assert nom.nomenclador_nacional_id is not None, "el código de prueba tiene que tener NN"
    e1, e2, e3 = await _tres_especialidades(db)
    nom.sin_restriccion_especialidad = None
    await aplicar_plantilla.reemplazar_plantilla(db, nom.codigo, [e1, e2])

    r = await service.generar_valores_nn_por_rangos(os_nueva, VIGENCIA, db, permitir_valor_cero=True)
    assert r["habilitaciones_sembradas"] >= 1
    assert await service.especialidades_habilitadas_de(db, nom.codigo, os_nueva) == {e1, e2}

    # Edición manual + regeneración: no se pisa.
    await service.reemplazar_especialidades(db, os_nueva, nom.codigo, [e3])
    await service.generar_valores_nn_por_rangos(os_nueva, VIGENCIA, db, permitir_valor_cero=True)
    assert await service.especialidades_habilitadas_de(db, nom.codigo, os_nueva) == {e3}


@pytest.mark.asyncio
async def test_generar_nn_respeta_sin_restriccion_del_catalogo(sin_commit):
    db = sin_commit
    os_nueva = 990_022
    await service.crear_galenos_base(os_nueva, VIGENCIA, db)
    nom = await db.get(NomencladorCMC, NOMENCLADOR_ID)
    nom.sin_restriccion_especialidad = True

    await service.generar_valores_nn_por_rangos(os_nueva, VIGENCIA, db, permitir_valor_cero=True)

    assert await service.par_sin_restriccion(db, nom.codigo, os_nueva)
    assert await service.especialidades_habilitadas_de(db, nom.codigo, os_nueva) == set()


@pytest.mark.asyncio
async def test_sembrar_nomenclador_nuevo_con_vigencia_1900(sin_commit):
    from app.db.models.nomenclador_cmc import Galeno, HistorialPrecioCodigo
    from app.modules.catalogs.routes_obras_sociales import VIGENCIA_NOMENCLADOR_INICIAL

    db = sin_commit
    os_nueva = 990_031
    r = await service.sembrar_nomenclador_nuevo(os_nueva, VIGENCIA_NOMENCLADOR_INICIAL, db)
    assert r["creados"] > 0 and not r["errores"]

    galenos = (await db.execute(select(Galeno).where(Galeno.obra_social_nro == os_nueva))).scalars().all()
    assert galenos and all(g.vigencia_desde == datetime.date(1900, 1, 1) for g in galenos)
    nn = await _activos(db, os_nueva)
    assert nn and all(v.vigencia_desde == datetime.date(1900, 1, 1) for v in nn)
    hist = (await db.execute(
        select(HistorialPrecioCodigo).where(HistorialPrecioCodigo.valores_id == nn[0].id)
    )).scalars().all()
    assert hist and hist[0].vigencia_desde == datetime.date(1900, 1, 1)
