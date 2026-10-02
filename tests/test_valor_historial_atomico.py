"""El alta de un `Valor` y su fila de historial son atómicas.

Por qué existe este archivo: el precio vive en dos tablas y el esquema no las
ata. `nm_valores` es lo que se edita y lo que muestra el panel;
`nm_historial_precio_codigo` es lo único contra lo que cotiza `lookup_precio`.
La FK entre ambas va en la dirección contraria a la que haría falta — obliga a
que todo historial apunte a un valor, no a que todo valor tenga historial — así
que "valor activo sin historial" es un estado perfectamente legal para la base,
y completamente silencioso: si hay una fila NN vigente para el mismo código,
ésta gana por descarte y la prestación se factura al precio nacional.

Pasó de verdad: los 520 valores NE de Prevención Salud (OS 103) quedaron sin
historial desde junio de 2026. 70 códigos cotizaron mal en silencio durante tres
meses y se detectó mirando un honorario a ojo.

Dos defensas, una por test:

* `test_ningun_valor_se_crea_fuera_del_punto_unico` mira el CÓDIGO. Antes, el
  alta y su historial eran dos llamadas que cada endpoint encadenaba por su
  cuenta en una decena de lugares; alcanzaba con que un camino nuevo se olvidara
  del segundo. Ahora hay un solo camino y este test falla el build si alguien
  abre otro.
* `test_no_hay_valores_activos_sin_historial` mira el DATO, por si algo entró
  por fuera de la aplicación (una migración, un import SQL a mano).
"""
import ast
from pathlib import Path

import pytest
from sqlalchemy import text

RAIZ = Path(__file__).resolve().parent.parent

#: Módulos que pueden construir un `Valor`. Cualquier otro que lo haga es un
#: camino de alta nuevo que se saltea el punto único.
MODULOS = (
    "app/modules/nomenclador/service.py",
    "app/modules/nomenclador/routes_valores.py",
)

#: Funciones que tienen permitido construir un `Valor` porque ellas mismas
#: generan el historial antes de devolverlo.
FUNCIONES_HABILITADAS = {
    "persistir_valor",               # service.py — el punto único
    "_crear_valor_con_componentes",  # routes_valores.py — alta desde el panel
    "_clonar_valor",                 # routes_valores.py — cerrar y recrear
}

#: Llamada que recibe el `Valor` recién construido como argumento; construirlo
#: ahí adentro es la forma correcta desde cualquier otro lado.
PUNTO_UNICO = "persistir_valor"


def _rangos_habilitados(arbol: ast.Module) -> list[tuple[int, int]]:
    """Tramos de líneas donde `Valor(...)` es legítimo."""
    rangos: list[tuple[int, int]] = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if nodo.name in FUNCIONES_HABILITADAS:
                rangos.append((nodo.lineno, nodo.end_lineno))
        elif isinstance(nodo, ast.Call):
            # `service.persistir_valor(...)` o `persistir_valor(...)`
            f = nodo.func
            nombre = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if nombre == PUNTO_UNICO:
                rangos.append((nodo.lineno, nodo.end_lineno))
    return rangos


def _construcciones_de_valor(arbol: ast.Module) -> list[int]:
    """Líneas donde se hace `Valor(...)` (la construcción, no `Valor.columna`)."""
    return [
        nodo.lineno
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.Call)
        and isinstance(nodo.func, ast.Name)
        and nodo.func.id == "Valor"
    ]


def test_ningun_valor_se_crea_fuera_del_punto_unico():
    fugas: list[str] = []
    vistos = 0

    for rel in MODULOS:
        ruta = RAIZ / rel
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        rangos = _rangos_habilitados(arbol)
        for linea in _construcciones_de_valor(arbol):
            vistos += 1
            if not any(ini <= linea <= fin for ini, fin in rangos):
                fugas.append(f"{rel}:{linea}")

    # Si esto da 0, el test dejó de mirar lo que cree mirar (se renombró la
    # clase, se movió el módulo) y pasaría en verde sin proteger nada.
    assert vistos > 0, "no se encontró ninguna construcción de Valor — ¿se movió el código?"

    assert not fugas, (
        "Hay altas de Valor fuera del punto único:\n  "
        + "\n  ".join(fugas)
        + f"\n\nTodo alta tiene que pasar por `service.{PUNTO_UNICO}(...)`, que "
          "escribe el valor, sus componentes y su fila de historial en la misma "
          "transacción. Un Valor sin historial se ve en el panel pero no existe "
          "para `lookup_precio`: la prestación se factura con otra variante o "
          "queda sin precio, y nadie se entera. Ver OS 103 / septiembre 2026."
    )


#: Única función que puede hacer `insert(Valor)` (alta en bloque): escribe los
#: componentes y el historial de todo el lote antes de devolver.
PUNTO_UNICO_EN_BLOQUE = "persistir_valores_en_bloque"


def _inserts_de_valor(arbol: ast.Module) -> list[int]:
    """Líneas con `insert(Valor)` (Core/ORM bulk insert): saltea `persistir_valor`
    igual que un `Valor(...)` suelto, y el test de arriba no lo ve."""
    return [
        nodo.lineno
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.Call)
        and getattr(nodo.func, "id", getattr(nodo.func, "attr", None)) == "insert"
        and nodo.args
        and isinstance(nodo.args[0], ast.Name)
        and nodo.args[0].id == "Valor"
    ]


def test_ningun_insert_en_bloque_de_valor_fuera_del_punto_unico():
    fugas: list[str] = []
    vistos = 0
    for ruta in sorted((RAIZ / "app").rglob("*.py")):
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        rangos = [
            (n.lineno, n.end_lineno) for n in ast.walk(arbol)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == PUNTO_UNICO_EN_BLOQUE
        ]
        for linea in _inserts_de_valor(arbol):
            vistos += 1
            if not any(ini <= linea <= fin for ini, fin in rangos):
                fugas.append(f"{ruta.relative_to(RAIZ).as_posix()}:{linea}")

    assert vistos > 0, "no se encontró el insert en bloque de Valor — ¿se movió el código?"
    assert not fugas, (
        "Hay `insert(Valor)` fuera del punto único en bloque:\n  "
        + "\n  ".join(fugas)
        + f"\n\nLas altas masivas tienen que pasar por `service.{PUNTO_UNICO_EN_BLOQUE}`, "
          "que escribe valores, componentes e historial del lote juntos."
    )


@pytest.mark.asyncio
async def test_no_hay_valores_activos_sin_historial(db):
    """El invariante sobre el dato. Tiene que dar 0."""
    filas = (await db.execute(text("""
        SELECT v.obra_social_nro, COUNT(*) AS n
        FROM nm_valores v
        WHERE v.estado = 'activo'
          AND NOT EXISTS (
                SELECT 1 FROM nm_historial_precio_codigo h WHERE h.valores_id = v.id
          )
        GROUP BY v.obra_social_nro
        ORDER BY n DESC
    """))).all()

    assert not filas, (
        "Valores activos sin fila de historial, por obra social: "
        + ", ".join(f"OS {os_nro}: {n}" for os_nro, n in filas)
        + ". Esos precios se ven en el panel pero son invisibles para "
          "`lookup_precio`. Diagnóstico completo en "
          "GET /api/valores_nm/diagnostico/sin_historial."
    )
