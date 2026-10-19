"""Nomencladores con escala propia: el galeno de la O.S. se crea al aplicar (calculado o precio
fijo por nivel), las unidades salen de la escala (con ajustes por O.S.), el cambio Cirugía
adulto 7 ↔ 10 elimina y recrea los valores, y la escala se edita y se recalcula.

Sesión sin commit (`commit` → `flush`, `rollback` → no-op): todo se descarta al cerrar.
Obra social de prueba 990_094; nomencladores de prueba de 2 y 3 niveles con el mismo galeno.
"""
import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db.models.catalogs import ObrasSociales
from app.db.models.nomenclador_cmc import (
    Galeno,
    NomencladorCMC,
    NomencladorNivel,
    NomencladorNivelado,
    NomencladorNiveladoCodigo,
    Valor,
    ValorComponente,
)
from app.modules.nomenclador import nivelados
from app.modules.nomenclador.schemas import (
    AplicarNiveladoAjuste,
    AplicarNiveladoGaleno,
    NiveladoEscalaIn,
    NiveladoEscalaNivel,
)

OS = 990_094
VIG = datetime.date(2026, 11, 1)
GALENO = "galeno_prueba_escala"
D = Decimal


@pytest.fixture
def s(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)

    async def _no_rollback():
        return None

    monkeypatch.setattr(db, "rollback", _no_rollback)
    return db


async def _preparar(db):
    """Dos nomencladores con el mismo galeno: `chico` (2 niveles) y `grande` (3). Códigos A y B
    en los dos; C sólo en el chico; D sólo en el grande."""
    db.add(ObrasSociales(NRO_OBRASOCIAL=OS, OBRA_SOCIAL="PRUEBA ESCALA", cuit="0"))
    chico = NomencladorNivelado(slug="prueba_esc_chico", nombre="Prueba chico", galeno_grupo="galeno_urologia",
                                galeno_codigo=GALENO, niveles=2)
    grande = NomencladorNivelado(slug="prueba_esc_grande", nombre="Prueba grande", galeno_grupo="galeno_urologia",
                                 galeno_codigo=GALENO, niveles=3)
    db.add_all([chico, grande])
    await db.flush()
    db.add_all([
        NomencladorNivel(nomenclador_nivelado_id=chico.id, nivel=1, unidades_honorarios=D("10")),
        NomencladorNivel(nomenclador_nivelado_id=chico.id, nivel=2, unidades_honorarios=D("20"), unidades_ayudante=D("6")),
        NomencladorNivel(nomenclador_nivelado_id=grande.id, nivel=1, unidades_honorarios=D("1")),
        NomencladorNivel(nomenclador_nivelado_id=grande.id, nivel=2, unidades_honorarios=D("2")),
        NomencladorNivel(nomenclador_nivelado_id=grande.id, nivel=3, unidades_honorarios=D("4")),
    ])
    a, b, c, d = list((await db.execute(
        select(NomencladorCMC).where(NomencladorCMC.activo == True).order_by(NomencladorCMC.id).limit(4)  # noqa: E712
    )).scalars())
    for cod in (a, b, c, d):
        cod.sin_restriccion_especialidad = True
    db.add_all([
        NomencladorNiveladoCodigo(nomenclador_nivelado_id=chico.id, nomenclador_id=a.id, nivel=2),
        NomencladorNiveladoCodigo(nomenclador_nivelado_id=chico.id, nomenclador_id=b.id, nivel=1),
        NomencladorNiveladoCodigo(nomenclador_nivelado_id=chico.id, nomenclador_id=c.id, nivel=1),
        NomencladorNiveladoCodigo(nomenclador_nivelado_id=grande.id, nomenclador_id=a.id, nivel=3),
        NomencladorNiveladoCodigo(nomenclador_nivelado_id=grande.id, nomenclador_id=b.id, nivel=2),
        NomencladorNiveladoCodigo(nomenclador_nivelado_id=grande.id, nomenclador_id=d.id, nivel=1),
    ])
    await db.flush()
    return chico, grande, (a, b, c, d)


async def _activos(db, nom_id):
    return list((await db.execute(select(Valor).where(
        Valor.obra_social_nro == OS, Valor.nomenclador_id == nom_id, Valor.estado == "activo",
    ))).scalars())


async def _comps(db, valor_id):
    return {c.concepto: c for c in (await db.execute(select(ValorComponente).where(
        ValorComponente.valor_id == valor_id, ValorComponente.activo == True,  # noqa: E712
    ))).scalars()}


async def _galenos_vigentes(db):
    return {g.nivel: g for g in (await db.execute(select(Galeno).where(
        Galeno.obra_social_nro == OS, Galeno.codigo == GALENO, Galeno.activo == True,  # noqa: E712
    ))).scalars()}


def _calculado(vu="100", **kw):
    return AplicarNiveladoGaleno(valor_unitario=D(vu), **kw)


@pytest.mark.asyncio
async def test_sin_galeno_y_sin_datos_pide_el_galeno(s):
    chico, *_ = await _preparar(s)
    with pytest.raises(HTTPException) as e:
        await nivelados.aplicar(s, chico.slug, OS, VIG, dry_run=True, usuario="t")
    assert e.value.status_code == 409 and e.value.detail["requiere_galeno"] is True


@pytest.mark.asyncio
async def test_aplicar_crea_el_galeno_calculado_y_las_unidades_salen_de_la_escala(s):
    chico, _, (a, b, c, d) = await _preparar(s)
    previa = await nivelados.aplicar(s, chico.slug, OS, VIG, dry_run=True, usuario="t", galeno=_calculado())
    assert previa.galeno_creado and await _galenos_vigentes(s) == {}   # la vista previa no escribe
    assert {f.codigo: f.precio for f in previa.filas}[a.codigo] == D("2600.00")  # (20 + 6) x 100 + gastos 0

    out = await nivelados.aplicar(s, chico.slug, OS, VIG, dry_run=False, usuario="t", galeno=_calculado())
    assert out.galeno_creado and out.resumen.crear == 3

    g = await _galenos_vigentes(s)
    assert sorted(g) == [1, 2] and all(x.valor_unitario == D("100") and not x.precio_por_nivel for x in g.values())
    assert g[1].unidades_honorarios is None  # sin ajuste: usa la escala del nomenclador

    [va] = await _activos(s, a.id)    # nivel 2: 20 de honorarios; ayudante 6 (fijado en la escala)
    comps = await _comps(s, va.id)
    assert (comps["Honorarios"].cantidad, comps["Ayudante"].cantidad) == (D("20"), D("6"))
    assert comps["Honorarios"].galeno_id == g[2].id and va.nomenclador_nivelado_id == chico.id
    [vb] = await _activos(s, b.id)    # nivel 1: ayudante por defecto = 25 % de 10
    assert (await _comps(s, vb.id))["Ayudante"].cantidad == D("2.50")


@pytest.mark.asyncio
async def test_precio_fijo_por_nivel(s):
    chico, _, (a, b, *_resto) = await _preparar(s)
    cfg = AplicarNiveladoGaleno(precio_por_nivel=True, precios_por_nivel={1: D("5000"), 2: D("9000")})
    await nivelados.aplicar(s, chico.slug, OS, VIG, dry_run=False, usuario="t", galeno=cfg)
    g = await _galenos_vigentes(s)
    assert g[1].valor_unitario == D("5000") and g[2].valor_unitario == D("9000") and g[2].precio_por_nivel
    [va] = await _activos(s, a.id)    # 1 unidad de honorarios + 25 % de ayudante sobre el precio del nivel 2
    comps = await _comps(s, va.id)
    assert (comps["Honorarios"].cantidad, comps["Ayudante"].cantidad) == (D("1"), D("0.25"))

    with pytest.raises(HTTPException) as e:     # falta el precio de un nivel
        await nivelados.aplicar(s, "prueba_esc_grande", OS, VIG + datetime.timedelta(days=30), dry_run=True,
                                usuario="t", galeno=AplicarNiveladoGaleno(precio_por_nivel=True, precios_por_nivel={1: D("1")}))
    assert e.value.status_code == 409 or e.value.status_code == 422


@pytest.mark.asyncio
async def test_ajuste_de_unidades_por_obra_social(s):
    chico, *_ = await _preparar(s)
    aj = [AplicarNiveladoAjuste(nivel=2, unidades_honorarios=D("7"), unidades_ayudante=D("1"))]
    await nivelados.aplicar(s, chico.slug, OS, VIG, dry_run=False, usuario="t", galeno=_calculado(ajustes=aj))
    g = await _galenos_vigentes(s)
    assert (g[2].unidades_honorarios, g[2].unidades_ayudante) == (D("7"), D("1")) and g[1].unidades_honorarios is None
    est = await nivelados.estado_os(s, chico.slug, OS)
    por_nivel = {n.nivel: n for n in est.niveles}
    assert est.galeno_estado == "ok" and por_nivel[2].ajustado and not por_nivel[1].ajustado
    assert por_nivel[2].unidades_honorarios == D("7")


@pytest.mark.asyncio
async def test_cambio_de_nomenclador_elimina_los_valores_del_anterior_y_crea_los_del_nuevo(s):
    chico, grande, (a, b, c, d) = await _preparar(s)
    await nivelados.aplicar(s, chico.slug, OS, VIG, dry_run=False, usuario="t", galeno=_calculado("100"))
    assert [len(await _activos(s, x.id)) for x in (a, b, c, d)] == [1, 1, 1, 0]

    est = await nivelados.estado_os(s, grande.slug, OS)
    assert est.galeno_estado == "otros_niveles" and est.niveles_os == 2
    assert (est.cambio.desde_slug, est.cambio.valores_eliminados, est.cambio.codigos_sin_reemplazo,
            est.cambio.valores_a_crear) == (chico.slug, 3, 1, 3)

    # Sin el precio del galeno nuevo no se puede.
    with pytest.raises(HTTPException) as e:
        await nivelados.aplicar(s, grande.slug, OS, VIG + datetime.timedelta(days=30), dry_run=True, usuario="t")
    assert e.value.status_code == 409 and e.value.detail["requiere_galeno"] is True

    nueva = VIG + datetime.timedelta(days=30)
    previa = await nivelados.aplicar(s, grande.slug, OS, nueva, dry_run=True, usuario="t", galeno=_calculado("1000"))
    assert previa.cambio.valores_eliminados == 3 and previa.cambio.valores_a_crear == 3
    assert len(await _galenos_vigentes(s)) == 2   # la vista previa no toca el galeno

    out = await nivelados.aplicar(s, grande.slug, OS, nueva, dry_run=False, usuario="t", galeno=_calculado("1000"))
    assert out.galeno_creado and out.cambio.codigos_sin_reemplazo == 1

    g = await _galenos_vigentes(s)
    assert sorted(g) == [1, 2, 3] and all(x.valor_unitario == D("1000") and x.vigencia_desde == nueva for x in g.values())
    viejos = list((await s.execute(select(Galeno).where(
        Galeno.obra_social_nro == OS, Galeno.codigo == GALENO, Galeno.activo == False,  # noqa: E712
    ))).scalars())
    assert len(viejos) == 2 and {x.vigencia_hasta for x in viejos} == {nueva - datetime.timedelta(days=1)}

    # A y B: ahora con el nomenclador grande (nivel 3 → 4 unidades). C (sólo en el chico): sin precio. D: nuevo.
    [va] = await _activos(s, a.id)
    assert va.nomenclador_nivelado_id == grande.id and (await _comps(s, va.id))["Honorarios"].cantidad == D("4")
    assert await _activos(s, c.id) == []
    assert len(await _activos(s, d.id)) == 1


@pytest.mark.asyncio
async def test_cambio_bloqueado_si_otro_valor_usa_el_galeno_viejo(s):
    chico, grande, (a, b, c, d) = await _preparar(s)
    await nivelados.aplicar(s, chico.slug, OS, VIG, dry_run=False, usuario="t", galeno=_calculado())
    # Un valor de un código que NO es del nomenclador chico, calculado con su galeno.
    ajeno = (await s.execute(select(NomencladorCMC).where(NomencladorCMC.id.notin_([a.id, b.id, c.id, d.id])).limit(1))).scalar_one()
    g = await _galenos_vigentes(s)
    from app.modules.nomenclador import service
    await service.persistir_valor(
        s, Valor(obra_social_nro=OS, nomenclador_id=ajeno.id, origen="NE", codigo=ajeno.codigo, descripcion="X",
                 sin_restriccion_especialidad=True, vigencia_desde=VIG, estado="activo"),
        [dict(concepto="Honorarios", galeno_id=g[1].id, cantidad=D("1"), orden=0)],
        motivo="carga_inicial", fecha_corte=None,
    )
    with pytest.raises(HTTPException) as e:
        await nivelados.aplicar(s, grande.slug, OS, VIG + datetime.timedelta(days=30), dry_run=False, usuario="t",
                                galeno=_calculado("1000"))
    assert e.value.status_code == 409 and ajeno.codigo in e.value.detail["mensaje"]
    assert len(await _galenos_vigentes(s)) == 2   # no se tocó nada


@pytest.mark.asyncio
async def test_el_cambio_exige_una_vigencia_posterior_a_la_del_galeno(s):
    chico, grande, _ = await _preparar(s)
    await nivelados.aplicar(s, chico.slug, OS, VIG, dry_run=False, usuario="t", galeno=_calculado())
    with pytest.raises(HTTPException) as e:
        await nivelados.aplicar(s, grande.slug, OS, VIG, dry_run=True, usuario="t", galeno=_calculado())
    assert e.value.status_code == 422


@pytest.mark.asyncio
async def test_escala_se_edita_y_recalcular_actualiza_los_valores(s):
    chico, _, (a, *_resto) = await _preparar(s)
    await nivelados.aplicar(s, chico.slug, OS, VIG, dry_run=False, usuario="t", galeno=_calculado("100"))

    with pytest.raises(HTTPException) as e:   # tiene que traer todos los niveles
        await nivelados.guardar_escala(s, chico.slug, NiveladoEscalaIn(niveles=[NiveladoEscalaNivel(nivel=1, unidades_honorarios=D("1"))]))
    assert e.value.status_code == 422

    esc = await nivelados.guardar_escala(s, chico.slug, NiveladoEscalaIn(niveles=[
        NiveladoEscalaNivel(nivel=1, unidades_honorarios=D("10")),
        NiveladoEscalaNivel(nivel=2, unidades_honorarios=D("40")),
    ]))
    assert esc.obras_sociales >= 1 and [n.unidades_honorarios for n in esc.niveles] == [D("10"), D("40")]
    # El valor ya creado no cambia hasta recalcular.
    [va] = await _activos(s, a.id)
    assert (await _comps(s, va.id))["Honorarios"].cantidad == D("20")

    nueva = VIG + datetime.timedelta(days=30)
    previa = await nivelados.recalcular(s, chico.slug, nueva, dry_run=True, usuario="t")
    assert any(r["obra_social_nro"] == OS and r["reemplazar"] == 3 for r in previa["detalle"])
    await nivelados.recalcular(s, chico.slug, nueva, dry_run=False, usuario="t")
    [va] = await _activos(s, a.id)
    comps = await _comps(s, va.id)
    assert va.vigencia_desde == nueva and (comps["Honorarios"].cantidad, comps["Ayudante"].cantidad) == (D("40"), D("10"))


@pytest.mark.asyncio
async def test_alta_de_un_valor_por_nomenclador_arma_los_componentes_con_la_escala(s):
    chico, grande, (a, b, c, d) = await _preparar(s)
    # Sin el galeno de la O.S. cargado no se puede armar.
    r = await nivelados.componentes_para_codigo(s, chico.slug, OS, a.id)
    assert not r.disponible and "Aplicá el nomenclador" in r.motivo

    await nivelados.aplicar(s, chico.slug, OS, VIG, dry_run=False, usuario="t",
                            galeno=_calculado("100", ajustes=[AplicarNiveladoAjuste(nivel=1, unidades_honorarios=D("12"))]))
    r = await nivelados.componentes_para_codigo(s, chico.slug, OS, b.id)   # b: nivel 1, con ajuste de 12 un.
    assert r.disponible and r.nivel == 1 and r.nomenclador_nivelado_id == chico.id and r.precio_por_nivel is False
    cant = {c.concepto: c.cantidad for c in r.componentes}
    assert cant["Honorarios"] == D("12") and cant["Ayudante"] == D("3.00")

    r = await nivelados.componentes_para_codigo(s, chico.slug, OS, d.id)   # d no es del chico
    assert not r.disponible and "no pertenece" in r.motivo

    disponibles = await nivelados.nomencladores_de_codigo(s, a.id)
    assert {x.slug for x in disponibles} >= {chico.slug, grande.slug}
    assert next(x for x in disponibles if x.slug == grande.slug).nivel_del_codigo == 3


@pytest.mark.asyncio
async def test_el_nacional_se_lista_y_no_se_aplica(s):
    todos = await nivelados.listar(s)
    nac = next(n for n in todos if n.tipo == "nacional")
    assert nac.slug == "nacional" and nac.niveles == 0 and nac.total_codigos > 0
    with pytest.raises(HTTPException) as e:
        await nivelados.aplicar(s, "nacional", OS, VIG, dry_run=True, usuario="t")
    assert e.value.status_code == 409


@pytest.mark.asyncio
async def test_resumen_de_galenos_marca_modo_nomenclador_y_niveles_ajustados(s):
    chico, *_ = await _preparar(s)
    aj = [AplicarNiveladoAjuste(nivel=2, unidades_honorarios=D("7"))]
    await nivelados.aplicar(s, chico.slug, OS, VIG, dry_run=False, usuario="t", galeno=_calculado(ajustes=aj))
    [r] = [x for x in await nivelados.resumen_galenos(s, OS) if x.codigo == GALENO]
    assert r.modo == "calculado" and r.nomencladores == [chico.nombre, "Prueba grande"]
    assert [(n.nivel, n.ajustado) for n in r.niveles] == [(1, False), (2, True)]

    await nivelados.aplicar(s, "prueba_esc_grande", OS, VIG + datetime.timedelta(days=30), dry_run=False, usuario="t",
                            galeno=AplicarNiveladoGaleno(precio_por_nivel=True, precios_por_nivel={1: D("1"), 2: D("2"), 3: D("3")}))
    [r] = [x for x in await nivelados.resumen_galenos(s, OS) if x.codigo == GALENO]
    assert r.modo == "por_nivel" and len(r.niveles) == 3 and not any(n.ajustado for n in r.niveles)


# ─── Editar un valor conserva (o cambia) con qué nomenclador se factura ───────────────

def _ecuacion(vigencia, comps, **kw):
    from app.modules.nomenclador.schemas import ValorCerrarYCrearIn, ValorComponenteIn
    return ValorCerrarYCrearIn(
        vigencia_desde=vigencia,
        componentes=[ValorComponenteIn(concepto=c, galeno_id=g, cantidad=q, valor_unitario=vu, orden=i)
                     for i, (c, g, q, vu) in enumerate(comps)],
        **kw,
    )


@pytest.mark.asyncio
async def test_editar_un_valor_por_nomenclador_conserva_el_nomenclador_y_se_puede_pasar_a_fijo_o_manual(s):
    from app.modules.nomenclador.routes_valores import _actualizar_valor_core

    chico, _, (a, b, c, d) = await _preparar(s)
    await nivelados.aplicar(s, chico.slug, OS, VIG, dry_run=False, usuario="t", galeno=_calculado())
    [va] = await _activos(s, a.id)
    g = await _galenos_vigentes(s)

    # 1) Rotar con otro precio sin decir nada: sigue facturándose por el mismo nomenclador.
    comps = [("Honorarios", g[2].id, D("20"), None), ("Ayudante", g[2].id, D("9"), None)]
    nuevo = await _actualizar_valor_core(s, va.id, _ecuacion(VIG + datetime.timedelta(days=10), comps))
    assert nuevo.nomenclador_nivelado_id == chico.id

    # 2) Pasarlo a manual (galeno × cantidad libres): deja de depender del nomenclador.
    manual = await _actualizar_valor_core(
        s, nuevo.id,
        _ecuacion(VIG + datetime.timedelta(days=20), comps, definir_nomenclador=True, nomenclador_nivelado_id=None),
    )
    assert manual.nomenclador_nivelado_id is None

    # 3) ... y volver al nomenclador.
    vuelta = await _actualizar_valor_core(
        s, manual.id,
        _ecuacion(VIG + datetime.timedelta(days=30), comps, definir_nomenclador=True, nomenclador_nivelado_id=chico.id),
    )
    assert vuelta.nomenclador_nivelado_id == chico.id

    # 4) Valor fijo: no puede quedar atado a un nomenclador, ni heredarlo.
    fijo = [("Honorarios", None, D("0"), D("5000"))]
    with pytest.raises(HTTPException) as e:
        await _actualizar_valor_core(
            s, vuelta.id,
            _ecuacion(VIG + datetime.timedelta(days=40), fijo, definir_nomenclador=True, nomenclador_nivelado_id=chico.id),
        )
    assert e.value.status_code == 422
    como_fijo = await _actualizar_valor_core(s, vuelta.id, _ecuacion(VIG + datetime.timedelta(days=40), fijo))
    assert como_fijo.nomenclador_nivelado_id is None

    # 5) Un nomenclador que no existe se rechaza.
    with pytest.raises(HTTPException) as e:
        await _actualizar_valor_core(
            s, como_fijo.id,
            _ecuacion(VIG + datetime.timedelta(days=50), comps, definir_nomenclador=True, nomenclador_nivelado_id=987654),
        )
    assert e.value.status_code == 422


@pytest.mark.asyncio
async def test_la_lista_de_nomencladores_de_un_codigo_trae_el_id(s):
    chico, grande, (a, *_) = await _preparar(s)
    ids = {x.slug: x.id for x in await nivelados.nomencladores_de_codigo(s, a.id)}
    assert ids[chico.slug] == chico.id and ids[grande.slug] == grande.id
