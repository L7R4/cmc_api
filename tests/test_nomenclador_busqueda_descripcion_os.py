"""Buscar una práctica por nombre con la obra social elegida (Consulta de valores).

La descripción vive por obra social (`nm_valores`); la del catálogo está vacía en
muchos códigos (p. ej. 070660 «Escleroterapia»), así que buscar solo en el catálogo no
los encontraba: había que tipear el código.

Los datos se siembran con `flush` y nunca se commitean (el fixture `db` no commitea). La
OS es un número que no existe, para no cruzarse con ninguna variante real.
"""
import datetime
from decimal import Decimal

from app.db.models.nomenclador_cmc import Valor
from app.modules.nomenclador import service
from app.modules.nomenclador.routes_nomenclador import list_nomenclador

NOMENCLADOR_ID = 897           # 030801 — cualquier código real sirve
OS_SINTETICA = 990_002
NOMBRE = "ZZPRUEBA ESCLEROTERAPIA"


async def _sembrar(db):
    nom = await db.get(service.NomencladorCMC, NOMENCLADOR_ID)
    valor = Valor(
        obra_social_nro=OS_SINTETICA, nomenclador_id=NOMENCLADOR_ID, origen="NE",
        codigo=nom.codigo, descripcion=NOMBRE, especialidad_id_colegio=None,
        sin_restriccion_especialidad=True, vigencia_desde=datetime.date(2026, 7, 1),
        estado="activo",
    )
    await service.persistir_valor(
        db, valor,
        [dict(concepto=c, galeno_id=None, cantidad=Decimal("0"), valor_unitario=Decimal("1000"), orden=i)
         for i, c in enumerate(("Honorarios", "Gastos", "Ayudante"))],
        motivo="carga_inicial", fecha_corte=None,
    )
    return nom.codigo


async def _buscar(db, q, obra_social_nro):
    return await list_nomenclador(
        q=q, en_descripcion=True, categoria=None, tipo=None, complejidad=None,
        obra_social_nro=obra_social_nro, activo=True, page=1, size=15, db=db,
        dep=(None, None, "admin"),
    )


async def test_encuentra_por_la_descripcion_que_pacto_la_os(db):
    codigo = await _sembrar(db)

    for q in ("zzprueba escleroterapia", "ZZPRUEBA ESCLEROTERAPÍA", "escleroterapia"):
        r = await _buscar(db, q, OS_SINTETICA)
        assert [(n.codigo, n.descripcion) for n in r if n.codigo == codigo] == [(codigo, NOMBRE)], q

    # El código sigue andando, y la descripción mostrada es la de la OS.
    r = await _buscar(db, codigo, OS_SINTETICA)
    assert next(n.descripcion for n in r if n.codigo == codigo) == NOMBRE


async def test_sin_la_os_no_aparece_por_un_nombre_ajeno_al_catalogo(db):
    await _sembrar(db)
    assert await _buscar(db, "zzprueba", None) == []
    # Otra obra social tampoco: la descripción es de esta.
    assert await _buscar(db, "zzprueba", OS_SINTETICA + 1) == []
