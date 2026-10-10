"""Importar valores fijos NE desde Excel (`importar_fijos`).

Sesión sin commit (`commit` → `flush`, `rollback` → no-op): todo se descarta al
cerrar. Obra social de prueba 990_096; códigos reales del catálogo, con alta,
plantilla y precios creados acá.
"""
import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db.models.catalogs import Especialidad, ObrasSociales
from app.db.models.nomenclador_cmc import (
    CodigoObraSocial,
    HistorialPrecioCodigo,
    NomencladorCMC,
    Valor,
    ValorComponente,
)
from app.modules.nomenclador import alta_os, aplicar_plantilla, importar_fijos
from app.modules.nomenclador.routes_valores import _crear_valor_con_componentes
from app.modules.nomenclador.schemas import (
    AltaCodigoItem,
    AltaCodigosIn,
    ImportarFijosAplicarIn,
    ImportarFijosIn,
    ValorComponenteIn,
)

OS = 990_096
VIG = datetime.date(2026, 10, 1)
ENC = ["Codigo", "Descripción", "valor"]
NUEVO = "ZZ9001"


@pytest.fixture
def s(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)

    async def _no_rollback():
        return None

    monkeypatch.setattr(db, "rollback", _no_rollback)
    return db


# ─── Parseo ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, esperado", [
    (37965, Decimal("37965.00")),
    (37965.5, Decimal("37965.50")),
    ("$ 140.775", Decimal("140775.00")),
    ("$ 2.347.599", Decimal("2347599.00")),
    ("1.234,50", Decimal("1234.50")),
    ("1234,5", Decimal("1234.50")),
    ("61826", Decimal("61826.00")),
])
def test_parsear_valor_numeros(raw, esperado):
    assert importar_fijos.parsear_valor(raw) == (esperado, False, None)


@pytest.mark.parametrize("raw", ["PRESUPUESTO", "presupuesto", " Presupuesto "])
def test_parsear_valor_presupuesto(raw):
    assert importar_fijos.parsear_valor(raw) == (None, True, None)


@pytest.mark.parametrize("raw", [None, "", "abc", "$ 12.34.5", 0, -5, "0"])
def test_parsear_valor_invalido(raw):
    importe, presupuesto, error = importar_fijos.parsear_valor(raw)
    assert importe is None and not presupuesto and error


def test_encabezado_sin_importar_mayusculas_ni_tildes():
    importar_fijos.validar_encabezado(["codigo", "DESCRIPCIÓN", " Valor ", None, ""])
    for malo in (["CODIGO", "VALOR", "DESCRIPCION"], ["CODIGO", "DESCRIPCION"],
                 ["CODIGO", "DESCRIPCION", "VALOR", "OTRA"]):
        with pytest.raises(HTTPException) as e:
            importar_fijos.validar_encabezado(malo)
        assert e.value.status_code == 422


# ─── Clasificación y aplicación ───────────────────────────────────────────────

async def _preparar(db):
    db.add(ObrasSociales(NRO_OBRASOCIAL=OS, OBRA_SOCIAL="PRUEBA VALORES FIJOS", cuit="0"))
    assert (await db.execute(select(NomencladorCMC).where(NomencladorCMC.codigo == NUEVO))).first() is None
    codigos = list((await db.execute(
        select(NomencladorCMC).where(NomencladorCMC.activo == True).order_by(NomencladorCMC.id).limit(5)
    )).scalars())
    e1, e2 = (await db.execute(
        select(Especialidad.ID_COLEGIO_ESPE).order_by(Especialidad.ID_COLEGIO_ESPE).limit(2)
    )).scalars().all()
    a, b, c, d, e = codigos
    for cod in codigos:
        cod.sin_restriccion_especialidad = None
        cod.categoria = "Practica"
        await aplicar_plantilla.reemplazar_plantilla(db, cod.codigo, [e1])
    await aplicar_plantilla.reemplazar_plantilla(db, b.codigo, [e1, e2])
    await db.flush()
    # b sin alta; el resto, dados de alta (c sin nadie que lo facture).
    await alta_os.dar_de_alta(db, AltaCodigosIn(items=[
        AltaCodigoItem(obra_social_nro=OS, nomenclador_id=n.id, especialidades=[e1]) for n in (a, d, e)
    ] + [AltaCodigoItem(obra_social_nro=OS, nomenclador_id=c.id, especialidades=[])]), "t")
    for nom, desde, monto in ((a, VIG - datetime.timedelta(days=60), "100"),
                              (d, VIG, "200"),
                              (e, VIG - datetime.timedelta(days=60), "300"),
                              (e, VIG + datetime.timedelta(days=30), "350")):
        await _crear_valor_con_componentes(
            db=db, obra_social_nro=OS, nomenclador_id=nom.id, origen="NE", vigencia_desde=desde,
            componentes_in=[ValorComponenteIn(concepto="Honorarios", valor_unitario=Decimal(monto))],
            descripcion="X", nivel=None, complejidad=None, especialidad_id_colegio=e1, observacion=None,
        )
        if nom is e and desde < VIG:
            # La vigencia posterior cierra la anterior (como haría la carga real).
            prev = (await db.execute(select(Valor).where(
                Valor.obra_social_nro == OS, Valor.nomenclador_id == e.id, Valor.estado == "activo",
            ))).scalar_one()
            prev.estado, prev.vigencia_hasta = "cerrado", VIG + datetime.timedelta(days=29)
            await db.flush()
    await db.flush()
    return (a, b, c, d, e), (e1, e2)


def _filas(a, b, c, d, e):
    return [
        {"fila": 2, "codigo": a.codigo, "descripcion": "A", "valor": "$ 1.100"},
        {"fila": 3, "codigo": b.codigo, "descripcion": "B excel", "valor": "PRESUPUESTO"},
        {"fila": 4, "codigo": c.codigo, "descripcion": "C", "valor": 500},
        {"fila": 5, "codigo": d.codigo, "descripcion": "D", "valor": "250"},
        {"fila": 6, "codigo": e.codigo, "descripcion": "E", "valor": "400"},
        {"fila": 7, "codigo": NUEVO, "descripcion": "Código nuevo", "valor": "$ 7.000"},
        {"fila": 8, "codigo": "", "descripcion": "sin código", "valor": "1"},
        {"fila": 9, "codigo": a.codigo, "descripcion": "valor roto", "valor": "abc"},
    ]


async def _activos(db, nom_id):
    return list((await db.execute(select(Valor).where(
        Valor.obra_social_nro == OS, Valor.nomenclador_id == nom_id, Valor.estado == "activo",
    ).order_by(Valor.especialidad_id_colegio))).scalars())


async def _comps(db, valor_id):
    return {c.concepto: c.valor_unitario for c in (await db.execute(select(ValorComponente).where(
        ValorComponente.valor_id == valor_id, ValorComponente.activo == True,
    ))).scalars()}


@pytest.mark.asyncio
async def test_previsualizar_clasifica_cada_estado_y_no_escribe(s):
    (a, b, c, d, e), (e1, e2) = await _preparar(s)
    out = await importar_fijos.previsualizar(s, ImportarFijosIn(
        obra_social_nro=OS, vigencia_desde=VIG, encabezado=ENC, filas=_filas(a, b, c, d, e),
    ))
    por_fila = {f.fila: f for f in out.filas}
    assert {k: (v.estado, v.accion_sugerida) for k, v in por_fila.items()} == {
        2: ("rotar", "rotar"),
        3: ("sin_alta", "alta_y_cargar"),
        4: ("sin_quien_factura", None),
        5: ("misma_vigencia", "sobrescribir"),
        6: ("vigente_posterior", "omitir"),
        7: ("sin_catalogo", "omitir"),
        8: ("error", "omitir"),
        9: ("error", "omitir"),
    }
    assert por_fila[2].valor == Decimal("1100.00")
    assert por_fila[2].variantes[0].precio_actual == Decimal("100.00")
    assert por_fila[2].variantes[0].variacion_pct == 1000.0
    assert por_fila[3].por_presupuesto and por_fila[3].especialidades == [e1, e2]
    assert por_fila[4].requiere_quien_factura and por_fila[7].requiere_quien_factura
    assert por_fila[6].variantes[0].posteriores == 1
    assert out.por_estado["error"] == 2
    assert await _activos(s, b.id) == []
    assert (await alta_os.get_par(s, OS, b.id)) is None


@pytest.mark.asyncio
async def test_aplicar_carga_rota_da_de_alta_y_crea_codigo(s):
    (a, b, c, d, e), (e1, e2) = await _preparar(s)
    filas = _filas(a, b, c, d, e)
    decisiones = [
        {"fila": 2, "estado_visto": "rotar", "accion": "rotar"},
        {"fila": 3, "estado_visto": "sin_alta", "accion": "alta_y_cargar"},
        {"fila": 4, "estado_visto": "sin_quien_factura", "accion": "cargar", "especialidades": [e2]},
        {"fila": 5, "estado_visto": "misma_vigencia", "accion": "sobrescribir"},
        {"fila": 6, "estado_visto": "vigente_posterior", "accion": "reemplazar"},
        {"fila": 7, "estado_visto": "sin_catalogo", "accion": "crear_y_cargar",
         "sin_restriccion": True, "categoria": "Practica"},
    ]
    out = await importar_fijos.aplicar(s, ImportarFijosAplicarIn(
        obra_social_nro=OS, vigencia_desde=VIG, encabezado=ENC, filas=filas, decisiones=decisiones,
    ), "t")
    assert (out.filas_cargadas, out.altas, out.codigos_creados, out.omitidas) == (6, 2, 1, 2)
    assert out.precios_creados == 7  # b va a 2 especialidades
    assert out.filas_rotadas == 3 and out.vigencias_borradas == 2

    # a: se cerró la anterior el día previo y se abrió la nueva, sólo Honorarios.
    va = await _activos(s, a.id)
    assert len(va) == 1 and va[0].vigencia_desde == VIG and va[0].especialidad_id_colegio == e1
    assert await _comps(s, va[0].id) == {
        "Honorarios": Decimal("1100.00"), "Gastos": Decimal("0.00"), "Ayudante": Decimal("0.00"),
    }
    cerrada = (await s.execute(select(Valor).where(
        Valor.obra_social_nro == OS, Valor.nomenclador_id == a.id, Valor.estado == "cerrado",
    ))).scalar_one()
    assert cerrada.vigencia_hasta == VIG - datetime.timedelta(days=1)
    hist = (await s.execute(select(HistorialPrecioCodigo).where(
        HistorialPrecioCodigo.valores_id == va[0].id,
    ))).scalar_one()
    assert hist.motivo_cambio == "valor_fijo_actualizado" and hist.precio_total == Decimal("1100.00")

    # b: alta con la plantilla, por presupuesto, una variante por especialidad.
    vb = await _activos(s, b.id)
    assert [v.especialidad_id_colegio for v in vb] == [e1, e2]
    assert all(v.por_presupuesto for v in vb)
    assert set((await _comps(s, vb[0].id)).values()) == {Decimal("0.00")}
    par_b = await alta_os.get_par(s, OS, b.id)
    assert par_b.estado == "activo" and par_b.descripcion == "B excel"
    hist_b = (await s.execute(select(HistorialPrecioCodigo).where(
        HistorialPrecioCodigo.valores_id == vb[0].id,
    ))).scalar_one()
    assert hist_b.motivo_cambio == "carga_inicial"

    # c: se habilitó la especialidad elegida.
    vc = await _activos(s, c.id)
    assert [v.especialidad_id_colegio for v in vc] == [e2]

    # d: misma vigencia → reemplazada, no duplicada.
    vd = list((await s.execute(select(Valor).where(
        Valor.obra_social_nro == OS, Valor.nomenclador_id == d.id,
    ))).scalars())
    assert len(vd) == 1 and (await _comps(s, vd[0].id))["Honorarios"] == Decimal("250.00")

    # e: la posterior se borró; queda la nueva como última.
    ve = list((await s.execute(select(Valor).where(
        Valor.obra_social_nro == OS, Valor.nomenclador_id == e.id,
    ).order_by(Valor.vigencia_desde))).scalars())
    assert [(v.vigencia_desde, v.estado) for v in ve] == [
        (VIG - datetime.timedelta(days=60), "cerrado"), (VIG, "activo"),
    ]
    assert ve[0].vigencia_hasta == VIG - datetime.timedelta(days=1)

    # Código nuevo: catálogo + alta sin restricción + precio sin especialidad.
    nuevo = (await s.execute(select(NomencladorCMC).where(NomencladorCMC.codigo == NUEVO))).scalar_one()
    assert nuevo.descripcion == "Código nuevo" and nuevo.categoria == "Practica"
    par_n = (await s.execute(select(CodigoObraSocial).where(
        CodigoObraSocial.obra_social_nro == OS, CodigoObraSocial.nomenclador_id == nuevo.id,
    ))).scalar_one()
    assert par_n.sin_restriccion_especialidad
    vn = await _activos(s, nuevo.id)
    assert len(vn) == 1 and vn[0].especialidad_id_colegio is None
    assert (await _comps(s, vn[0].id))["Honorarios"] == Decimal("7000.00")


@pytest.mark.asyncio
async def test_aplicar_rechaza_si_cambio_el_estado_o_la_accion_no_vale(s):
    (a, b, c, d, e), _ = await _preparar(s)
    filas = _filas(a, b, c, d, e)[:2]

    def body(decisiones):
        return ImportarFijosAplicarIn(obra_social_nro=OS, vigencia_desde=VIG, encabezado=ENC,
                                      filas=filas, decisiones=decisiones)

    with pytest.raises(HTTPException) as ex:
        await importar_fijos.aplicar(s, body([{"fila": 2, "estado_visto": "nuevo", "accion": "cargar"}]), "t")
    assert ex.value.status_code == 409
    with pytest.raises(HTTPException) as ex:
        await importar_fijos.aplicar(s, body([{"fila": 2, "estado_visto": "rotar", "accion": "crear_y_cargar"}]), "t")
    assert ex.value.status_code == 422
    assert await _activos(s, b.id) == []


@pytest.mark.asyncio
async def test_aplicar_exige_decidir_quien_factura_y_duplicados(s):
    (a, b, c, d, e), (e1, _) = await _preparar(s)
    filas = [
        {"fila": 2, "codigo": c.codigo, "descripcion": "C", "valor": 500},
        {"fila": 3, "codigo": a.codigo, "descripcion": "A1", "valor": 10},
        {"fila": 4, "codigo": a.codigo, "descripcion": "A2", "valor": 20},
    ]
    previa = await importar_fijos.previsualizar(s, ImportarFijosIn(
        obra_social_nro=OS, vigencia_desde=VIG, encabezado=ENC, filas=filas,
    ))
    assert [(f.estado, f.estado_base) for f in previa.filas] == [
        ("sin_quien_factura", None), ("duplicado", "rotar"), ("duplicado", "rotar"),
    ]

    def body(decisiones):
        return ImportarFijosAplicarIn(obra_social_nro=OS, vigencia_desde=VIG, encabezado=ENC,
                                      filas=filas, decisiones=decisiones)

    # Sin decidir quién factura ni cuál de las repetidas → 422.
    with pytest.raises(HTTPException) as ex:
        await importar_fijos.aplicar(s, body([]), "t")
    assert ex.value.status_code == 422 and len(ex.value.detail["errores"]) == 3
    # Las dos repetidas a la vez → 422.
    with pytest.raises(HTTPException) as ex:
        await importar_fijos.aplicar(s, body([
            {"fila": 2, "estado_visto": "sin_quien_factura", "accion": "omitir"},
            {"fila": 3, "estado_visto": "duplicado", "accion": "rotar"},
            {"fila": 4, "estado_visto": "duplicado", "accion": "rotar"},
        ]), "t")
    assert ex.value.status_code == 422

    out = await importar_fijos.aplicar(s, body([
        {"fila": 2, "estado_visto": "sin_quien_factura", "accion": "cargar", "sin_restriccion": True},
        {"fila": 3, "estado_visto": "duplicado", "accion": "omitir"},
        {"fila": 4, "estado_visto": "duplicado", "accion": "rotar"},
    ]), "t")
    assert out.filas_cargadas == 2
    va = await _activos(s, a.id)
    assert (await _comps(s, va[0].id))["Honorarios"] == Decimal("20.00")
    vc = await _activos(s, c.id)
    assert len(vc) == 1 and vc[0].especialidad_id_colegio is None and vc[0].sin_restriccion_especialidad


# ─── Réplica en la familia (obras sociales adicionales) ──────────────────────

ADICIONAL = 990_097


@pytest.mark.parametrize("estado, accion_origen, sin_cambio, esperado", [
    ("nuevo", "cargar", False, "cargar"),
    ("rotar", "rotar", False, "rotar"),
    ("rotar", "rotar", True, None),
    ("sin_alta", "rotar", False, "alta_y_cargar"),
    ("sin_catalogo", "crear_y_cargar", False, "alta_y_cargar"),
    ("sin_quien_factura", "cargar", False, "cargar"),
    ("suspendido", "cargar", False, None),
    ("vigente_posterior", "rotar", False, None),
    ("vigente_posterior", "reemplazar", False, "reemplazar"),
    ("misma_vigencia", "rotar", False, None),
    ("misma_vigencia", "sobrescribir", False, "sobrescribir"),
])
def test_accion_en_destino(estado, accion_origen, sin_cambio, esperado):
    assert importar_fijos.accion_en_destino(estado, accion_origen, sin_cambio)[0] == esperado


async def _con_adicional(db, a, e1):
    principal = (await db.execute(select(ObrasSociales).where(ObrasSociales.NRO_OBRASOCIAL == OS))).scalar_one()
    db.add(ObrasSociales(NRO_OBRASOCIAL=ADICIONAL, OBRA_SOCIAL="PRUEBA ADICIONAL", cuit="0",
                         obra_social_principal_id=principal.ID))
    await db.flush()
    # En la adicional, `a` ya tiene una vigencia posterior a la que se carga: no se pisa.
    await alta_os.dar_de_alta(db, AltaCodigosIn(items=[
        AltaCodigoItem(obra_social_nro=ADICIONAL, nomenclador_id=a.id, especialidades=[e1]),
    ]), "t")
    await _crear_valor_con_componentes(
        db=db, obra_social_nro=ADICIONAL, nomenclador_id=a.id, origen="NE",
        vigencia_desde=VIG + datetime.timedelta(days=30),
        componentes_in=[ValorComponenteIn(concepto="Honorarios", valor_unitario=Decimal("999"))],
        descripcion="X", nivel=None, complejidad=None, especialidad_id_colegio=e1, observacion=None,
    )


@pytest.mark.asyncio
async def test_sin_familia_no_ofrece_replicar(s):
    (a, b, c, d, e), _ = await _preparar(s)
    out = await importar_fijos.previsualizar(s, ImportarFijosIn(
        obra_social_nro=OS, vigencia_desde=VIG, encabezado=ENC, filas=_filas(a, b, c, d, e)[:2],
    ))
    assert out.familia == [] and all(f.replicas == [] for f in out.filas)


@pytest.mark.asyncio
async def test_replica_en_la_obra_social_adicional(s):
    (a, b, c, d, e), (e1, e2) = await _preparar(s)
    await _con_adicional(s, a, e1)
    filas = _filas(a, b, c, d, e)[:2]   # a (rotar) y b (sin alta, PRESUPUESTO)

    previa = await importar_fijos.previsualizar(s, ImportarFijosIn(
        obra_social_nro=OS, vigencia_desde=VIG, encabezado=ENC, filas=filas,
    ))
    assert [f.nro_obra_social for f in previa.familia] == [ADICIONAL]
    rep = {f.fila: f.replicas[0] for f in previa.filas}
    assert (rep[2].estado, rep[2].accion) == ("vigente_posterior", None)
    assert (rep[3].estado, rep[3].accion) == ("sin_alta", "alta_y_cargar")

    out = await importar_fijos.aplicar(s, ImportarFijosAplicarIn(
        obra_social_nro=OS, vigencia_desde=VIG, encabezado=ENC, filas=filas, replicar_en=[ADICIONAL],
        decisiones=[{"fila": 2, "estado_visto": "rotar", "accion": "rotar"},
                    {"fila": 3, "estado_visto": "sin_alta", "accion": "alta_y_cargar"}],
    ), "t")
    assert out.filas_cargadas == 2
    (r,) = out.replicas
    assert (r.obra_social_nro, r.estado, r.filas_cargadas, r.altas, r.precios_creados) == (ADICIONAL, "ok", 1, 1, 2)
    assert [(o.fila, o.motivo) for o in r.omitidas] == [(2, "Tiene una vigencia posterior")]

    # b en la adicional: dado de alta con las mismas especialidades que en la principal.
    vb = list((await s.execute(select(Valor).where(
        Valor.obra_social_nro == ADICIONAL, Valor.nomenclador_id == b.id, Valor.estado == "activo",
    ).order_by(Valor.especialidad_id_colegio))).scalars())
    assert [v.especialidad_id_colegio for v in vb] == [e1, e2] and all(v.por_presupuesto for v in vb)
    # a en la adicional: intacto.
    va = list((await s.execute(select(Valor).where(
        Valor.obra_social_nro == ADICIONAL, Valor.nomenclador_id == a.id,
    ))).scalars())
    assert [(v.vigencia_desde, v.estado) for v in va] == [(VIG + datetime.timedelta(days=30), "activo")]


@pytest.mark.asyncio
async def test_replicar_en_una_os_fuera_de_la_familia_da_422(s):
    (a, b, c, d, e), _ = await _preparar(s)
    with pytest.raises(HTTPException) as ex:
        await importar_fijos.aplicar(s, ImportarFijosAplicarIn(
            obra_social_nro=OS, vigencia_desde=VIG, encabezado=ENC, filas=_filas(a, b, c, d, e)[:1],
            replicar_en=[81], decisiones=[{"fila": 2, "estado_visto": "rotar", "accion": "rotar"}],
        ), "t")
    assert ex.value.status_code == 422
