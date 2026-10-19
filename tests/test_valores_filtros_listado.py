"""Filtros del listado de precios de una obra social (`GET /api/valores_nm/`): `q`, `tipo`,
`por_presupuesto` y `especialidad_habilitada`, resueltos en SQL para que la pantalla de precios no baje
toda la obra social.

Sesión sin commit (`commit` → `flush`): todo se descarta al cerrar. Obra social de prueba 990_097.
"""
import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models.catalogs import Especialidad, ObrasSociales
from app.db.models.nomenclador_cmc import NomencladorCMC
from app.modules.nomenclador import alta_os, aplicar_plantilla
from app.modules.nomenclador.routes_valores import _crear_valor_con_componentes, list_valores
from app.modules.nomenclador.schemas import AltaCodigoItem, AltaCodigosIn, ValorComponenteIn

OS = 990_097
VIG = datetime.date(2026, 10, 1)


@pytest.fixture
async def os_con_precios(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)
    db.add(ObrasSociales(NRO_OBRASOCIAL=OS, OBRA_SOCIAL="PRUEBA FILTROS LISTADO", cuit="0"))
    codigos = list((await db.execute(
        select(NomencladorCMC).where(NomencladorCMC.activo == True).order_by(NomencladorCMC.id).limit(4)
    )).scalars())
    e1, e2 = (await db.execute(
        select(Especialidad.ID_COLEGIO_ESPE).order_by(Especialidad.ID_COLEGIO_ESPE).limit(2)
    )).scalars().all()
    consulta, practica, honorarios, presupuesto = codigos
    for cod, categoria, esp in ((consulta, "Consulta", e1), (practica, "Practica", e2),
                                (honorarios, "Honorarios individuales", e1), (presupuesto, "Practica", e2)):
        cod.categoria = categoria
        cod.sin_restriccion_especialidad = None
        await aplicar_plantilla.reemplazar_plantilla(db, cod.codigo, [esp])
    await db.flush()
    await alta_os.dar_de_alta(db, AltaCodigosIn(items=[
        AltaCodigoItem(obra_social_nro=OS, nomenclador_id=n.id, especialidades=[esp])
        for n, esp in ((consulta, e1), (practica, e2), (honorarios, e1), (presupuesto, e2))
    ]), "t")
    for nom, esp, es_presupuesto in ((consulta, e1, False), (practica, e2, False),
                                     (honorarios, e1, False), (presupuesto, e2, True)):
        await _crear_valor_con_componentes(
            db=db, obra_social_nro=OS, nomenclador_id=nom.id, origen="NE", vigencia_desde=VIG,
            componentes_in=[] if es_presupuesto else [ValorComponenteIn(concepto="Honorarios", valor_unitario=Decimal("100"))],
            descripcion=f"DESC {nom.codigo}", nivel=None, complejidad=None, especialidad_id_colegio=esp,
            observacion=None, por_presupuesto=es_presupuesto,
        )
    await db.flush()
    return {"consulta": consulta, "practica": practica, "honorarios": honorarios, "presupuesto": presupuesto,
            "e1": e1, "e2": e2}


async def _listar(db, **filtros):
    args = dict(
        obra_social_nro=OS, codigo=None, nivel=None, especialidad_id_colegio=None, estado="activo",
        vigente_a=None, vigencia_desde=None, q=None, tipo=None, por_presupuesto=None,
        especialidad_habilitada=None, page=1, size=200, db=db,
    )
    valores = await list_valores(**{**args, **filtros})
    return {v.codigo for v in valores}


@pytest.mark.asyncio
async def test_sin_filtros_trae_todo_y_cada_filtro_acota(db, os_con_precios):
    d = os_con_precios
    todos = {d[k].codigo for k in ("consulta", "practica", "honorarios", "presupuesto")}
    assert await _listar(db) == todos
    assert await _listar(db, tipo="Consulta") == {d["consulta"].codigo}
    assert await _listar(db, tipo="Honorarios individuales") == {d["honorarios"].codigo}
    assert await _listar(db, tipo="Practica") == {d["practica"].codigo, d["presupuesto"].codigo}
    assert await _listar(db, por_presupuesto=True) == {d["presupuesto"].codigo}
    assert await _listar(db, por_presupuesto=False) == todos - {d["presupuesto"].codigo}
    assert await _listar(db, especialidad_habilitada=d["e1"]) == {d["consulta"].codigo, d["honorarios"].codigo}
    assert await _listar(db, especialidad_habilitada=d["e2"]) == {d["practica"].codigo, d["presupuesto"].codigo}


@pytest.mark.asyncio
async def test_busqueda_por_codigo_o_descripcion_y_combinacion(db, os_con_precios):
    d = os_con_precios
    assert await _listar(db, q=d["practica"].codigo) == {d["practica"].codigo}
    assert await _listar(db, q=f"desc {d['honorarios'].codigo}") == {d["honorarios"].codigo}
    assert await _listar(db, q="no-existe-xyz") == set()
    # Los filtros se combinan (AND).
    assert await _listar(db, tipo="Practica", por_presupuesto=True, especialidad_habilitada=d["e2"]) == {
        d["presupuesto"].codigo,
    }
    assert await _listar(db, tipo="Consulta", por_presupuesto=True) == set()
