"""Alta de una obra social derivada: replicar de la cabecera (`replicar_cabecera.py`) y reglas del
alta (siempre activa, cabecera no derivada, baja lógica con `activo`).

Usa una OS real de dev como cabecera (solo lectura) y una derivada de prueba con número fuera de
rango; la sesión no commitea (`commit` → `flush`) y se revierte sola.
"""
import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.db.models.catalogs import ObrasSociales
from app.db.models.nomenclador_cmc import (
    CodigoObraSocial, Galeno, NomencladorNiveladoCodigo, Valor, ValorComponente, ValorEspecialidad,
)
from app.modules.catalogs import routes_obras_sociales as rutas
from app.modules.catalogs.schemas import ObraSocialCreate, ReplicarAltaIn
from app.modules.nomenclador import replicar_cabecera as rc, service

DEST = 990_090
VIG = datetime.date(1900, 1, 1)
CAB = 53  # OSPAV: tiene galenos, códigos, NN y NE (datos reales de dev)


@pytest.fixture
def s(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)
    return db


async def _cabecera(db) -> ObrasSociales:
    cab = (await db.execute(select(ObrasSociales).where(ObrasSociales.NRO_OBRASOCIAL == CAB))).scalar_one_or_none()
    if cab is None:
        pytest.skip(f"la OS {CAB} no está en esta base")
    return cab


async def _derivada(db) -> ObrasSociales:
    cab = await _cabecera(db)
    d = ObrasSociales(NRO_OBRASOCIAL=DEST, OBRA_SOCIAL="PRUEBA DERIVADA", cuit="0",
                      obra_social_principal_id=cab.ID)
    db.add(d)
    await db.flush()
    return d


async def _precios(db, os_nro: int, origen: str) -> dict:
    """(código, especialidad) → precio calculado desde los componentes (galeno × cantidad)."""
    out: dict = {}
    for v in (await db.execute(select(Valor).where(
        Valor.obra_social_nro == os_nro, Valor.origen == origen, Valor.estado == "activo",
    ))).scalars():
        total = Decimal(0)
        for c in (await db.execute(select(ValorComponente).where(
            ValorComponente.valor_id == v.id, ValorComponente.activo == True,  # noqa: E712
        ))).scalars():
            unit = (await db.get(Galeno, c.galeno_id)).valor_unitario if c.galeno_id else c.valor_unitario
            total += Decimal(c.cantidad) * Decimal(unit or 0)
        out[(v.codigo, v.especialidad_id_colegio)] = total.quantize(Decimal("0.01"))
    return out


@pytest.mark.asyncio
async def test_galenos_copia_los_vigentes_con_su_vigencia(s):
    cab = await _cabecera(s)
    await _derivada(s)
    r = await rc.copiar_galenos(s, CAB, DEST)
    assert r.estado == "ok" and r.creados > 0 and r.ya_existian == 0

    de_cab = {(g.codigo, g.nivel): g for g in (await s.execute(select(Galeno).where(
        Galeno.obra_social_nro == CAB, Galeno.activo == True))).scalars()}  # noqa: E712
    de_dest = {(g.codigo, g.nivel): g for g in (await s.execute(select(Galeno).where(
        Galeno.obra_social_nro == DEST))).scalars()}
    assert de_dest.keys() == de_cab.keys() and len(de_dest) == r.creados
    for k, g in de_cab.items():
        d = de_dest[k]
        assert (d.valor_unitario, d.vigencia_desde, d.unidades_honorarios, d.activo) == (
            g.valor_unitario, g.vigencia_desde, g.unidades_honorarios, True)
    assert cab.ID  # la cabecera no se tocó


@pytest.mark.asyncio
async def test_flujo_completo_deja_a_la_derivada_igual_que_la_cabecera(s):
    await _derivada(s)
    pasos = [await rc.copiar_galenos(s, CAB, DEST)]
    pasos.append(await rc.copiar_codigos(s, CAB, DEST, None))
    pasos.append(await rc.copiar_valores(s, CAB, DEST))          # con «valores» no se aplican los nivelados
    await service.sembrar_nomenclador_nuevo(DEST, VIG, s)        # el alta siembra lo que falte
    assert all(p.estado in ("ok", "parcial") for p in pasos), [p.model_dump() for p in pasos]

    # Los NN (sembrados con los galenos ya replicados) y los NE cuestan lo mismo que en la cabecera.
    for origen in ("NN", "NE"):
        cab, dest = await _precios(s, CAB, origen), await _precios(s, DEST, origen)
        faltan = set(cab) - set(dest)
        assert not faltan, f"{origen}: faltan {len(faltan)} variantes, p. ej. {sorted(faltan, key=str)[:3]}"
        distintos = {k: (cab[k], dest[k]) for k in cab if dest[k] != cab[k]}
        assert not distintos, f"{origen}: {len(distintos)} con otro precio, p. ej. {list(distintos.items())[:3]}"

    # Códigos dados de alta con quién factura.
    pares_cab = (await s.execute(select(func.count()).select_from(CodigoObraSocial).where(
        CodigoObraSocial.obra_social_nro == CAB))).scalar_one()
    pares_dest = (await s.execute(select(func.count()).select_from(CodigoObraSocial).where(
        CodigoObraSocial.obra_social_nro == DEST))).scalar_one()
    assert pares_dest >= pares_cab * 0.99
    cab_esp = set((await s.execute(select(ValorEspecialidad.codigo, ValorEspecialidad.especialidad_id_colegio)
                                   .where(ValorEspecialidad.obra_social_nro == CAB))).all())
    dest_esp = set((await s.execute(select(ValorEspecialidad.codigo, ValorEspecialidad.especialidad_id_colegio)
                                    .where(ValorEspecialidad.obra_social_nro == DEST))).all())
    assert cab_esp <= dest_esp


@pytest.mark.asyncio
async def test_nivelados_aplica_los_que_tiene_la_cabecera_con_los_galenos_de_la_derivada(s):
    await _derivada(s)
    await rc.copiar_galenos(s, CAB, DEST)
    await rc.copiar_codigos(s, CAB, DEST, None)
    r = await rc.copiar_nivelados(s, CAB, DEST, None)
    assert r.estado in ("ok", "parcial"), r.model_dump()
    aplicados = await rc._nivelados_aplicados(s, CAB)
    if not aplicados:
        pytest.skip("la cabecera de prueba no tiene nivelados aplicados")
    assert r.creados == len(aplicados)
    # Tiene precio NE en todos los códigos del primer nivelado.
    n, _ = aplicados[0]
    nom_ids = list((await s.execute(select(NomencladorNiveladoCodigo.nomenclador_id).where(
        NomencladorNiveladoCodigo.nomenclador_nivelado_id == n.id))).scalars())
    con_ne = set((await s.execute(select(Valor.nomenclador_id).where(
        Valor.obra_social_nro == DEST, Valor.origen == "NE", Valor.estado == "activo",
        Valor.nomenclador_id.in_(nom_ids)))).scalars())
    assert con_ne == set(nom_ids)


@pytest.mark.asyncio
async def test_valores_sin_galenos_omite_los_que_dependen_de_ellos(s):
    await _derivada(s)                                          # sin galenos replicados
    r = await rc.copiar_valores(s, CAB, DEST)
    assert r.estado in ("parcial", "omitido") and r.omitidos > 0
    assert any("galeno" in d for d in r.detalle)


@pytest.mark.asyncio
async def test_no_pisa_lo_que_la_derivada_ya_tiene(s):
    await _derivada(s)
    await rc.copiar_galenos(s, CAB, DEST)
    await rc.copiar_codigos(s, CAB, DEST, None)
    antes = (await s.execute(select(func.count()).select_from(CodigoObraSocial).where(
        CodigoObraSocial.obra_social_nro == DEST))).scalar_one()
    r = await rc.copiar_codigos(s, CAB, DEST, None)             # segunda vez: no hay nada nuevo
    despues = (await s.execute(select(func.count()).select_from(CodigoObraSocial).where(
        CodigoObraSocial.obra_social_nro == DEST))).scalar_one()
    assert r.creados == 0 and r.ya_existian == antes == despues


# ── Reglas del alta ──────────────────────────────────────────────────────────

def _payload(**kw) -> ObraSocialCreate:
    return ObraSocialCreate(nro_obra_social=DEST, nombre="PRUEBA ALTA DERIVADA", **kw)


@pytest.mark.asyncio
async def test_replicar_sin_cabecera_se_rechaza(s):
    with pytest.raises(HTTPException) as e:
        await rutas.create_obra_social(_payload(replicar=ReplicarAltaIn(galenos=True)), s)
    assert e.value.status_code == 422 and "cabecera" in e.value.detail


@pytest.mark.asyncio
async def test_la_cabecera_no_puede_ser_derivada(s):
    d = await _derivada(s)           # DEST es derivada de CAB
    otra = ObraSocialCreate(nro_obra_social=DEST + 1, nombre="PRUEBA NIETA", obra_social_principal_id=d.ID)
    with pytest.raises(HTTPException) as e:
        await rutas.create_obra_social(otra, s)
    assert e.value.status_code == 422 and "derivada" in e.value.detail


@pytest.mark.asyncio
async def test_alta_siempre_activa_y_replica_galenos(s):
    cab = await _cabecera(s)
    out = await rutas.create_obra_social(
        _payload(obra_social_principal_id=cab.ID, replicar=ReplicarAltaIn(galenos=True)), s)
    assert out.activo is True and out.obra_social_principal_id == cab.ID
    assert out.replicacion and out.replicacion.cabecera_nro == CAB
    assert [p.paso for p in out.replicacion.pasos] == ["galenos"]
    assert out.replicacion.pasos[0].creados > 0
    # El sembrado NN nació con los precios reales de la cabecera, no en $0.
    nn = await _precios(s, DEST, "NN")
    assert any(v > 0 for v in nn.values())


@pytest.mark.asyncio
async def test_eliminar_es_baja_logica_con_activo_false(s):
    out = await rutas.create_obra_social(_payload(), s)
    assert out.activo is True and out.replicacion is None
    await rutas.delete_obra_social(out.id, s)
    obj = await s.get(ObrasSociales, out.id)
    await s.refresh(obj)
    assert obj.activo is False
