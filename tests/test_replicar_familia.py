"""Replicar en las obras sociales de la misma familia (valores y galenos).

Familia sintética: principal 990_061 + asociadas 990_062 y 990_063, creadas en la
sesión y nunca commiteadas (`commit` → `flush`; el fixture `db` descarta todo).
"""
import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db.models.catalogs import Especialidad, ObrasSociales
from app.db.models.nomenclador_cmc import Galeno, NomencladorCMC, Valor
from app.modules.nomenclador import replicar_familia, service
from app.modules.nomenclador.schemas import (
    ReplicaAltaIn,
    ReplicaGalenoNivel,
    ReplicarFamiliaGalenoIn,
    ReplicarFamiliaValorIn,
    ReplicaVarianteIn,
    ValorCerrarYCrearIn,
    ValorComponenteIn,
    ValorNucleoUpdate,
)

P, A1, A2 = 990_061, 990_062, 990_063
NOM_ID = 897
VIG = datetime.date(2026, 7, 1)
NUEVA = datetime.date(2026, 8, 1)
GALENO = "galeno_prueba_familia"


@pytest.fixture
def s(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)
    return db


async def _familia(db):
    p = ObrasSociales(NRO_OBRASOCIAL=P, OBRA_SOCIAL="FAMILIA PRINCIPAL", cuit="0")
    db.add(p)
    await db.flush()
    for nro in (A1, A2):
        db.add(ObrasSociales(NRO_OBRASOCIAL=nro, OBRA_SOCIAL=f"FAMILIA PLAN {nro}",
                             cuit="0", obra_social_principal_id=p.ID))
    await db.flush()


async def _esp(db) -> int:
    return (await db.execute(select(Especialidad.ID_COLEGIO_ESPE).limit(1))).scalar_one()


async def _galeno(db, os_nro, vu="1000.00", vig=VIG, nivel=None) -> Galeno:
    g = Galeno(obra_social_nro=os_nro, codigo=GALENO, nombre="Galeno prueba familia",
               nivel=nivel, vigencia_desde=vig, valor_unitario=Decimal(vu), activo=True)
    db.add(g)
    await db.flush()
    return g


def _fijo(vu="24000"):
    return [ValorComponenteIn(concepto="Honorarios", valor_unitario=Decimal(vu))]


async def _activos(db, os_nro):
    return list((await db.execute(select(Valor).where(
        Valor.obra_social_nro == os_nro, Valor.nomenclador_id == NOM_ID, Valor.estado == "activo",
    ))).scalars())


def _total(v):
    return sum((c.subtotal for c in v.componentes if c.activo), Decimal("0"))


async def _alta_origen(db, esp, componentes=None):
    """Simula el alta ya hecha en la OS de origen."""
    from app.modules.nomenclador.routes_valores import _crear_valor_con_componentes

    await service.validar_especialidad_habilitada(db, (await db.get(NomencladorCMC, NOM_ID)).codigo, P, esp)
    return await _crear_valor_con_componentes(
        db=db, obra_social_nro=P, nomenclador_id=NOM_ID, origen="NE", vigencia_desde=VIG,
        componentes_in=componentes or _fijo(), descripcion="DESC", nivel=None, complejidad=None,
        especialidad_id_colegio=esp, observacion=None,
    )


def _estados(r):
    return {x.obra_social_nro: x.estado for x in r.resultados}


@pytest.mark.asyncio
async def test_familia_de_devuelve_las_otras(s):
    await _familia(s)
    assert {f.nro_obra_social for f in await replicar_familia.familia_de(s, A1)} == {P, A2}


@pytest.mark.asyncio
async def test_destino_fuera_de_la_familia_da_422(s):
    await _familia(s)
    with pytest.raises(HTTPException) as e:
        await replicar_familia.replicar_galenos(s, ReplicarFamiliaGalenoIn(
            origen_obra_social_nro=P, destinos=[433], operacion="precio", codigo=GALENO,
            vigencia_desde=NUEVA, nuevo_valor_unitario=Decimal("1"),
        ))
    assert e.value.status_code == 422


@pytest.mark.asyncio
async def test_alta_fija_se_copia_y_si_ya_existe_se_omite(s):
    await _familia(s)
    esp = await _esp(s)
    await _alta_origen(s, esp)
    body = ReplicarFamiliaValorIn(
        origen_obra_social_nro=P, nomenclador_id=NOM_ID, destinos=[A1, A2], operacion="alta",
        alta=ReplicaAltaIn(origen="NE", especialidades_id_colegio=[esp], descripcion="DESC",
                           vigencia_desde=VIG, componentes=_fijo()),
    )
    r = await replicar_familia.replicar_valores(s, body)
    assert _estados(r) == {A1: "replicado", A2: "replicado"}
    [v] = await _activos(s, A1)
    assert _total(v) == Decimal("24000") and v.especialidad_id_colegio == esp

    r2 = await replicar_familia.replicar_valores(s, body)
    assert _estados(r2) == {A1: "omitido", A2: "omitido"}


@pytest.mark.asyncio
async def test_alta_calculable_usa_el_galeno_del_destino_o_se_omite(s):
    await _familia(s)
    esp = await _esp(s)
    g_p = await _galeno(s, P, "1000.00")
    await _galeno(s, A1, "1500.00")  # A2 no tiene el galeno
    comps = [ValorComponenteIn(concepto="Honorarios", galeno_id=g_p.id, cantidad=Decimal("2"))]
    await _alta_origen(s, esp, comps)
    r = await replicar_familia.replicar_valores(s, ReplicarFamiliaValorIn(
        origen_obra_social_nro=P, nomenclador_id=NOM_ID, destinos=[A1, A2], operacion="alta",
        alta=ReplicaAltaIn(origen="NE", especialidades_id_colegio=[esp], descripcion="DESC",
                           vigencia_desde=VIG, componentes=comps),
    ))
    assert _estados(r) == {A1: "replicado", A2: "omitido"}
    [v] = await _activos(s, A1)
    assert _total(v) == Decimal("3000.00")


@pytest.mark.asyncio
async def test_variante_rota_y_si_falta_el_codigo_lo_crea(s):
    await _familia(s)
    esp = await _esp(s)
    await _alta_origen(s, esp)
    await replicar_familia.replicar_valores(s, ReplicarFamiliaValorIn(
        origen_obra_social_nro=P, nomenclador_id=NOM_ID, destinos=[A1], operacion="alta",
        alta=ReplicaAltaIn(origen="NE", especialidades_id_colegio=[esp], descripcion="DESC",
                           vigencia_desde=VIG, componentes=_fijo()),
    ))
    # En origen ya se rotó la variante a 30000 desde NUEVA.
    from app.modules.nomenclador.routes_valores import _actualizar_valor_core
    [v_p] = await _activos(s, P)
    ecu = ValorCerrarYCrearIn(vigencia_desde=NUEVA, componentes=_fijo("30000"))
    await _actualizar_valor_core(s, v_p.id, ecu)

    r = await replicar_familia.replicar_valores(s, ReplicarFamiliaValorIn(
        origen_obra_social_nro=P, nomenclador_id=NOM_ID, destinos=[A1, A2], operacion="variante",
        variante=ReplicaVarianteIn(origen="NE", especialidad_id_colegio=esp, ecuacion=ecu),
    ))
    assert _estados(r) == {A1: "replicado", A2: "creado"}
    [v1] = await _activos(s, A1)
    [v2] = await _activos(s, A2)
    assert v1.vigencia_desde == NUEVA and _total(v1) == Decimal("30000")
    assert _total(v2) == Decimal("30000")  # copia del estado actual de origen

    r2 = await replicar_familia.replicar_valores(s, ReplicarFamiliaValorIn(
        origen_obra_social_nro=P, nomenclador_id=NOM_ID, destinos=[A1], operacion="variante",
        variante=ReplicaVarianteIn(origen="NE", especialidad_id_colegio=esp, ecuacion=ecu),
    ))
    assert _estados(r2) == {A1: "omitido"}


@pytest.mark.asyncio
async def test_nucleo_replica_datos(s):
    await _familia(s)
    esp = await _esp(s)
    await _alta_origen(s, esp)
    await replicar_familia.replicar_valores(s, ReplicarFamiliaValorIn(
        origen_obra_social_nro=P, nomenclador_id=NOM_ID, destinos=[A1], operacion="alta",
        alta=ReplicaAltaIn(origen="NE", especialidades_id_colegio=[esp], descripcion="DESC",
                           vigencia_desde=VIG, componentes=_fijo()),
    ))
    r = await replicar_familia.replicar_valores(s, ReplicarFamiliaValorIn(
        origen_obra_social_nro=P, nomenclador_id=NOM_ID, destinos=[A1], operacion="nucleo",
        nucleo=ValorNucleoUpdate(descripcion="NUEVA DESC", especialidades=[esp]),
    ))
    assert _estados(r) == {A1: "replicado"}
    [v] = await _activos(s, A1)
    assert v.descripcion == "NUEVA DESC"


@pytest.mark.asyncio
async def test_galeno_precio_rota_crea_u_omite(s):
    await _familia(s)
    await _galeno(s, P, "1000.00")
    await _galeno(s, A1, "900.00")
    await _galeno(s, A2, "900.00", vig=NUEVA)
    r = await replicar_familia.replicar_galenos(s, ReplicarFamiliaGalenoIn(
        origen_obra_social_nro=P, destinos=[A1, A2], operacion="precio", codigo=GALENO,
        vigencia_desde=NUEVA, nuevo_valor_unitario=Decimal("1100.00"),
    ))
    assert _estados(r) == {A1: "replicado", A2: "omitido"}
    g1 = await service.buscar_galeno_vigente(s, A1, GALENO, None)
    assert g1.valor_unitario == Decimal("1100.00") and g1.vigencia_desde == NUEVA


@pytest.mark.asyncio
async def test_galeno_precio_lo_crea_si_falta_y_alta_omite_existentes(s):
    await _familia(s)
    await _galeno(s, P, "1000.00")
    r = await replicar_familia.replicar_galenos(s, ReplicarFamiliaGalenoIn(
        origen_obra_social_nro=P, destinos=[A1], operacion="precio", codigo=GALENO,
        vigencia_desde=NUEVA, nuevo_valor_unitario=Decimal("1100.00"),
    ))
    assert _estados(r) == {A1: "creado"}

    alta = ReplicarFamiliaGalenoIn(
        origen_obra_social_nro=P, destinos=[A1, A2], operacion="alta", codigo=GALENO,
        nombre="Galeno prueba familia", vigencia_desde=VIG,
        niveles=[ReplicaGalenoNivel(valor_unitario=Decimal("1000.00"))],
    )
    r2 = await replicar_familia.replicar_galenos(s, alta)
    assert _estados(r2) == {A1: "omitido", A2: "replicado"}


@pytest.mark.asyncio
async def test_galeno_unidades_omite_si_no_existe(s):
    await _familia(s)
    await _galeno(s, P)
    await _galeno(s, A1)
    r = await replicar_familia.replicar_galenos(s, ReplicarFamiliaGalenoIn(
        origen_obra_social_nro=P, destinos=[A1, A2], operacion="unidades", codigo=GALENO,
        vigencia_desde=NUEVA, unidades_honorarios=Decimal("3"),
    ))
    assert _estados(r) == {A1: "replicado", A2: "omitido"}
    g1 = await service.buscar_galeno_vigente(s, A1, GALENO, None)
    assert g1.unidades_honorarios == Decimal("3")


# ─── «Actualizar galenos»: precios en lote y visibilidad ─────────────────────

async def _gal(db, os_nro, codigo, vu, vig=VIG, nivel=None, visible=True) -> Galeno:
    g = Galeno(obra_social_nro=os_nro, codigo=codigo, nombre=f"Nombre {codigo}", nivel=nivel,
               vigencia_desde=vig, valor_unitario=Decimal(vu), activo=True, visible=visible,
               unidades_honorarios=Decimal("2") if nivel is not None else None)
    db.add(g)
    await db.flush()
    return g


async def _vigentes(db, os_nro, codigo):
    return list((await db.execute(select(Galeno).where(
        Galeno.obra_social_nro == os_nro, Galeno.codigo == codigo,
        Galeno.vigencia_hasta.is_(None), Galeno.activo.is_(True),
    ).order_by(Galeno.nivel))).scalars())


@pytest.mark.asyncio
async def test_lote_precios_rota_todos_los_niveles_y_crea_si_falta(s):
    from app.modules.nomenclador.schemas import ReplicaGalenoLoteItem, ReplicarFamiliaGalenosLoteIn

    await _familia(s)
    for n in (1, 2):
        await _gal(s, P, "gal_niv", "1100.00", vig=NUEVA, nivel=n)   # el origen ya quedó actualizado
        await _gal(s, A1, "gal_niv", "900.00", nivel=n)
    r = await replicar_familia.replicar_galenos_lote(s, ReplicarFamiliaGalenosLoteIn(
        origen_obra_social_nro=P, destinos=[A1, A2], operacion="precios", vigencia_desde=NUEVA,
        items=[ReplicaGalenoLoteItem(codigo="gal_niv", nuevo_valor_unitario=Decimal("1100.00"))],
    ))
    assert _estados(r) == {A1: "replicado", A2: "replicado"}
    por = {x.obra_social_nro: x for x in r.resultados}
    assert por[A2].motivo == "Se creó: gal_niv"

    for dest in (A1, A2):
        filas = await _vigentes(s, dest, "gal_niv")
        assert [f.nivel for f in filas] == [1, 2]
        assert all(f.valor_unitario == Decimal("1100.00") and f.vigencia_desde == NUEVA for f in filas)
        assert all(f.unidades_honorarios == Decimal("2") for f in filas)
    # El historial del plan que ya lo tenía queda cerrado un día antes.
    viejas = (await s.execute(select(Galeno).where(
        Galeno.obra_social_nro == A1, Galeno.codigo == "gal_niv", Galeno.activo.is_(False)))).scalars().all()
    assert len(viejas) == 2 and all(v.vigencia_hasta == NUEVA - datetime.timedelta(days=1) for v in viejas)


@pytest.mark.asyncio
async def test_lote_precios_parcial_cuenta_los_omitidos(s):
    from app.modules.nomenclador.schemas import ReplicaGalenoLoteItem, ReplicarFamiliaGalenosLoteIn

    await _familia(s)
    await _gal(s, P, "gal_a", "1100.00", vig=NUEVA)
    await _gal(s, P, "gal_b", "1100.00", vig=NUEVA)
    await _gal(s, A1, "gal_a", "900.00")
    await _gal(s, A1, "gal_b", "900.00", vig=NUEVA)       # ya tiene esa vigencia
    await _gal(s, A2, "gal_b", "900.00", vig=NUEVA)       # sólo tiene el que no se puede
    r = await replicar_familia.replicar_galenos_lote(s, ReplicarFamiliaGalenosLoteIn(
        origen_obra_social_nro=P, destinos=[A1, A2], operacion="precios", vigencia_desde=NUEVA,
        items=[ReplicaGalenoLoteItem(codigo="gal_a", nuevo_valor_unitario=Decimal("1100.00")),
               ReplicaGalenoLoteItem(codigo="gal_b", nuevo_valor_unitario=Decimal("1100.00"))],
    ))
    por = {x.obra_social_nro: x for x in r.resultados}
    assert por[A1].estado == "replicado" and "Omitidos: gal_b" in por[A1].motivo
    # A2: gal_a no lo tiene (se crea) y gal_b se omite → también cuenta como replicado.
    assert por[A2].estado == "replicado" and por[A2].motivo.startswith("Se creó: gal_a")
    assert (await _vigentes(s, A1, "gal_a"))[0].valor_unitario == Decimal("1100.00")
    assert (await _vigentes(s, A1, "gal_b"))[0].valor_unitario == Decimal("900.00")


@pytest.mark.asyncio
async def test_lote_visibilidad_pisa_todas_las_filas_y_omite_si_no_lo_tiene(s):
    from app.modules.nomenclador.schemas import ReplicaGalenoLoteItem, ReplicarFamiliaGalenosLoteIn

    await _familia(s)
    await _gal(s, P, "gal_v", "1000.00", visible=False)
    vieja = await _gal(s, A1, "gal_v", "900.00", vig=datetime.date(2026, 1, 1))
    vieja.activo, vieja.vigencia_hasta = False, VIG - datetime.timedelta(days=1)
    await _gal(s, A1, "gal_v", "950.00")
    r = await replicar_familia.replicar_galenos_lote(s, ReplicarFamiliaGalenosLoteIn(
        origen_obra_social_nro=P, destinos=[A1, A2], operacion="visibilidad",
        items=[ReplicaGalenoLoteItem(codigo="gal_v", visible=False)],
    ))
    assert _estados(r) == {A1: "replicado", A2: "omitido"}
    filas = (await s.execute(select(Galeno).where(Galeno.obra_social_nro == A1, Galeno.codigo == "gal_v"))).scalars().all()
    assert len(filas) == 2 and not any(f.visible for f in filas)


def test_lote_valida_el_pedido():
    from pydantic import ValidationError
    from app.modules.nomenclador.schemas import ReplicaGalenoLoteItem, ReplicarFamiliaGalenosLoteIn

    base = dict(origen_obra_social_nro=P, destinos=[A1])
    with pytest.raises(ValidationError):   # precios sin vigencia
        ReplicarFamiliaGalenosLoteIn(**base, operacion="precios",
                                     items=[ReplicaGalenoLoteItem(codigo="x", nuevo_valor_unitario=Decimal("1"))])
    with pytest.raises(ValidationError):   # visibilidad sin el valor
        ReplicarFamiliaGalenosLoteIn(**base, operacion="visibilidad", items=[ReplicaGalenoLoteItem(codigo="x")])
    with pytest.raises(ValidationError):   # códigos repetidos
        ReplicarFamiliaGalenosLoteIn(**base, operacion="visibilidad",
                                     items=[ReplicaGalenoLoteItem(codigo="x", visible=True)] * 2)
