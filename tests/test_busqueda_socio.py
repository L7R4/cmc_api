"""Buscador de socios compartido (`app/modules/medicos/busqueda.py`): qué coincide y en qué orden.

Reglas:
  - sólo números -> N° de socio, después nombre, después matrícula; con letras -> nombre, después matrícula
  - en cada campo, primero lo exacto y después lo parcial (empieza con / contiene)
  - sin distinguir mayúsculas ni acentos

No toca datos de verdad: arma una tabla TEMPORAL (vive sólo en la conexión del test) con la misma forma que
`listado_medico` y la misma collation de `NOMBRE`.
"""
import pytest
import pytest_asyncio
from sqlalchemy import Integer, String, column, select, table, text

from app.modules.medicos.busqueda import criterio_socio

SOCIOS = table(
    "tmp_busqueda_socios",
    column("ID", Integer), column("NRO_SOCIO", Integer), column("NOMBRE", String), column("MATRICULA_PROV", Integer),
)

# (ID, NRO_SOCIO, NOMBRE, MATRICULA_PROV). Los N° que no deben coincidir con «3» no llevan ese dígito.
FILAS = [
    (1, 3, "ABELENDA FERNANDO", 1713),
    (2, 30, "ZETA ANA", 500),
    (3, 31, "YANEZ LUIS", 501),
    (4, 300, "XIMENEZ JOSE", 600),
    (5, 13, "WALTER RAUL", 700),
    (6, 23, "VEGA ELSA", 701),
    (7, 60, "TRES 3 HERMANOS", 800),      # «3» en el nombre
    (8, 61, "PINSKER RAUL", 3),           # matrícula exacta 3
    (9, 62, "PEREZ JUAN", 30),            # matrícula que empieza con 3
    (10, 64, "GOMEZ ANA", 1300),          # matrícula que contiene 3
    (11, 70, "PEREZ   ", 1),              # nombre exacto (con espacios al final, como en la tabla real)
    (12, 71, "PEREZ JUAN CARLOS", 2),
    (13, 72, "JOSE PEREZ", 4),
    (14, 74, "JOSÉ SANCHEZ", 9),          # con acento
    (15, 75, "SANCHEZ DE LA ROSA JOSE", 8),
]


@pytest_asyncio.fixture
async def socios(db):
    await db.execute(text(
        "CREATE TEMPORARY TABLE tmp_busqueda_socios ("
        " ID INT, NRO_SOCIO INT, NOMBRE VARCHAR(40) CHARACTER SET utf8 COLLATE utf8_general_ci, MATRICULA_PROV INT)"
    ))
    for fila in FILAS:
        await db.execute(
            text("INSERT INTO tmp_busqueda_socios VALUES (:i, :n, :nom, :m)"),
            {"i": fila[0], "n": fila[1], "nom": fila[2], "m": fila[3]},
        )
    yield db
    await db.execute(text("DROP TEMPORARY TABLE IF EXISTS tmp_busqueda_socios"))


async def _buscar(db, texto):
    c = criterio_socio(texto, nro=SOCIOS.c.NRO_SOCIO, nombre=SOCIOS.c.NOMBRE, matricula=SOCIOS.c.MATRICULA_PROV)
    assert c is not None
    filas = (await db.execute(select(SOCIOS.c.NRO_SOCIO).where(c.condicion).order_by(*c.orden))).scalars().all()
    return list(filas)


def test_sin_texto_no_hay_criterio():
    assert criterio_socio(None) is None
    assert criterio_socio("") is None
    assert criterio_socio("   ") is None


async def test_numero_primero_nro_despues_nombre_y_al_final_matricula(socios):
    # Escribo «3»: el socio 3; los Nº que empiezan con 3 (de menor a mayor); los que lo contienen; el nombre; la matrícula.
    assert await _buscar(socios, "3") == [3, 30, 31, 300, 13, 23, 60, 61, 62, 64]


async def test_numero_exacto_gana_aunque_haya_mas_largos(socios):
    # «60»: el Nº 60, y recién después la matrícula 600 del socio 300.
    assert await _buscar(socios, "60") == [60, 300]


async def test_numero_que_solo_es_matricula(socios):
    assert await _buscar(socios, "1713") == [3]
    assert await _buscar(socios, "1300") == [64]
    assert await _buscar(socios, "500") == [30]


async def test_texto_primero_nombre_exacto_luego_empieza_y_luego_contiene(socios):
    # exacto (70), empiezan con (62 «PEREZ JUAN», 71 «PEREZ JUAN CARLOS»), contiene (72 «JOSE PEREZ»)
    assert await _buscar(socios, "perez") == [70, 62, 71, 72]


async def test_no_distingue_mayusculas_ni_acentos(socios):
    # «JOSÉ SANCHEZ» (74) es el nombre igual; «SANCHEZ DE LA ROSA JOSE» (75) tiene las dos palabras.
    assert await _buscar(socios, "jose sanchez") == [74, 75]
    assert await _buscar(socios, "JOSÉ SANCHEZ") == [74, 75]
    assert await _buscar(socios, "JOSE SANCHEZ") == [74, 75]


async def test_palabras_en_cualquier_orden(socios):
    assert await _buscar(socios, "juan perez") == [62, 71]


async def test_un_texto_no_busca_por_numero_de_socio(socios):
    # «ana» aparece en nombres; el socio con Nº 64 («GOMEZ ANA») y el 30 («ZETA ANA») salen por nombre, no por número.
    assert sorted(await _buscar(socios, "ana")) == [30, 64]


async def test_nada_que_coincida(socios):
    assert await _buscar(socios, "qqqq") == []
    assert await _buscar(socios, "99999") == []
