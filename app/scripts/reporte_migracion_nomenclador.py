"""
Reporte de solo lectura de la fase 1 de la reestructura del nomenclador.

Se corre DESPUÉS de `backfill_nomenclador_fase1.py` (o en cualquier momento
después, para auditar el estado actual). No escribe nada. Lista:

  1. Conflictos NN: codigo_nacional compartido por más de un código del Colegio,
     o choque de código propio — quién ganó y con qué números.
  2. Códigos con unidades_* pero sin codigo_nacional (usaron su propio código
     como NN) — para revisar si corresponde vincularlos a un NN real a mano.
  3. Pares (obra_social, código) con historial en detalle_facturacion pero sin
     ningún Valor activo en esa OS — dependen de nm_nomenclador_descripcion_legacy
     para mostrar descripción (ver obstáculo 2 del plan); no se resuelven acá.

Ejecutar:
  docker exec fastapi python app/scripts/reporte_migracion_nomenclador.py
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.db.database import AsyncSessionLocal, engine


async def reporte_conflictos_nn(db) -> None:
    print("\n=== 1. Conflictos NN (codigo_nacional compartido) ===")
    filas = (await db.execute(text(
        """
        SELECT nn.codigo, nn.id AS nn_id,
               GROUP_CONCAT(n.id ORDER BY n.id) AS colegio_ids,
               GROUP_CONCAT(DISTINCT n.descripcion SEPARATOR ' | ') AS descripciones
        FROM nm_nomenclador_nacional nn
        JOIN nm_nomenclador n ON n.nomenclador_nacional_id = nn.id
        GROUP BY nn.id
        HAVING COUNT(*) > 1
        ORDER BY COUNT(*) DESC
        """
    ))).all()
    print(f"{len(filas)} códigos NN agrupan más de un código del Colegio")
    for f in filas[:30]:
        print(f"  NN {f.codigo} (id {f.nn_id}) <- colegio ids [{f.colegio_ids}] :: {f.descripciones}")
    if len(filas) > 30:
        print(f"  ... y {len(filas) - 30} más")


async def reporte_sin_codigo_nacional(db) -> None:
    print("\n=== 2. Códigos con unidades_* sin codigo_nacional (NN sintético = su propio código) ===")
    filas = (await db.execute(text(
        """
        SELECT n.codigo, n.descripcion, n.nomenclador_nacional_id
        FROM nm_nomenclador n
        WHERE n.codigo_nacional IS NULL
          AND (n.unidades_honorarios IS NOT NULL OR n.unidades_ayudante IS NOT NULL
               OR n.unidades_gastos IS NOT NULL)
        ORDER BY n.codigo
        """
    ))).all()
    vinculados = sum(1 for f in filas if f.nomenclador_nacional_id is not None)
    print(f"{len(filas)} códigos totales, {vinculados} vinculados a su NN sintético")
    sin_vincular = [f for f in filas if f.nomenclador_nacional_id is None]
    if sin_vincular:
        print("  SIN vincular (revisar — probablemente chocaron con otro código):")
        for f in sin_vincular:
            print(f"    {f.codigo}: {f.descripcion}")


async def reporte_pares_sin_valor(db) -> None:
    print("\n=== 3. Pares (OS, código) facturados sin Valor activo en esa OS ===")
    print("(dependen de nm_nomenclador_descripcion_legacy para su descripción)")
    filas = (await db.execute(text(
        """
        SELECT x.cod_obr, x.cod_nom, COUNT(*) AS prestaciones
        FROM (
            SELECT DISTINCT f.id_detalle_prestaciones, f.cod_obr, f.cod_nom, f.nomenclador_id
            FROM detalle_facturacion f
            WHERE f.nomenclador_id IS NOT NULL AND f.cod_obr IS NOT NULL
        ) x
        WHERE NOT EXISTS (
            SELECT 1 FROM nm_valores v
            WHERE v.nomenclador_id = x.nomenclador_id
              AND v.obra_social_nro = x.cod_obr
              AND v.estado = 'activo'
        )
        GROUP BY x.cod_obr, x.cod_nom
        ORDER BY prestaciones DESC
        LIMIT 30
        """
    ))).all()
    total = (await db.execute(text(
        """
        SELECT COUNT(*) FROM (
            SELECT DISTINCT f.cod_obr, f.nomenclador_id
            FROM detalle_facturacion f
            WHERE f.nomenclador_id IS NOT NULL AND f.cod_obr IS NOT NULL
        ) x
        WHERE NOT EXISTS (
            SELECT 1 FROM nm_valores v
            WHERE v.nomenclador_id = x.nomenclador_id
              AND v.obra_social_nro = x.cod_obr
              AND v.estado = 'activo'
        )
        """
    ))).scalar_one()
    print(f"{total} pares en total. Los 30 con más prestaciones facturadas:")
    for f in filas:
        print(f"  OS {f.cod_obr} / código {f.cod_nom}: {f.prestaciones} prestaciones")


async def main() -> None:
    async with AsyncSessionLocal() as db:
        await reporte_conflictos_nn(db)
        await reporte_sin_codigo_nacional(db)
        await reporte_pares_sin_valor(db)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
