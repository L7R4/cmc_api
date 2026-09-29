"""
Fase 3 de la reestructura del nomenclador — paso previo obligatorio al DROP de
columnas: fusiona los códigos que todavía tienen más de una fila en
nm_nomenclador (remanente de cuando `obra_social_nro` distinguía una versión
"propia de una OS" de la "compartida" del Colegio — ver obstáculo 7 del plan).

Para cada código con >1 fila, elige una sobreviviente:
  1. Si hay EXACTAMENTE UNA fila activa entre las del grupo, esa gana (es la que
     de verdad se está usando).
  2. Si no, gana la compartida (obra_social_nro IS NULL).
  3. Si tampoco, la de id más chico (la más vieja).
Grupos con MÁS DE UNA fila activa son ambiguos de verdad — no se tocan, quedan
listados para revisar a mano.

Todo lo que colgaba de las filas perdedoras (Valor, HistorialPrecioCodigo,
MedicoCodigoHabilitado, Homologador, detalle_facturacion) se repunta a la
sobreviviente, y las filas perdedoras se borran. `nm_valor_especialidad` y
`nm_nomenclador_descripcion_legacy` no necesitan tocarse: ya cuelgan del string
`codigo`, no de `nomenclador_id` (por eso nunca hizo falta este merge en las
fases 1 y 2 — recién ahora, para poder crear UNIQUE(codigo), es obligatorio).

Correr en producción DESPUÉS del backup de las tablas nm_* y ANTES del DDL de
`scripts/nomenclador_fase3_<fecha>.sql` (que agrega UNIQUE(codigo) y falla si
queda algún duplicado sin fusionar).

Ejecutar:
  docker exec fastapi python -m app.scripts.fase3_merge_codigos_duplicados
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

from sqlalchemy import select, update

from app.db.database import AsyncSessionLocal, engine
from app.db.models import (
    DetalleFacturacionCMC, Homologador, HistorialPrecioCodigo,
    MedicoCodigoHabilitado, NomencladorCMC, Valor,
)


async def main() -> None:
    async with AsyncSessionLocal() as db:
        filas = (await db.execute(
            select(NomencladorCMC.id, NomencladorCMC.codigo, NomencladorCMC.obra_social_nro,
                   NomencladorCMC.activo)
            .order_by(NomencladorCMC.codigo, NomencladorCMC.id)
        )).all()

        grupos: dict[str, list] = defaultdict(list)
        for fila in filas:
            grupos[fila.codigo].append(fila)

        duplicados = {codigo: fs for codigo, fs in grupos.items() if len(fs) > 1}
        print(f"Códigos con más de una fila: {len(duplicados)}")

        ambiguos = []
        fusionados = 0
        repunteos = {"valores": 0, "historial": 0, "habilitaciones_medico": 0,
                     "homologador": 0, "detalle_facturacion": 0}

        for codigo, fs in duplicados.items():
            activas = [f for f in fs if f.activo]
            if len(activas) > 1:
                ambiguos.append({"codigo": codigo, "ids_activos": [f.id for f in activas]})
                continue
            if len(activas) == 1:
                sobreviviente = activas[0]
            else:
                compartidas = [f for f in fs if f.obra_social_nro is None]
                sobreviviente = compartidas[0] if compartidas else min(fs, key=lambda f: f.id)

            perdedoras_ids = [f.id for f in fs if f.id != sobreviviente.id]

            r1 = await db.execute(
                update(Valor).where(Valor.nomenclador_id.in_(perdedoras_ids))
                .values(nomenclador_id=sobreviviente.id)
            )
            r2 = await db.execute(
                update(HistorialPrecioCodigo).where(HistorialPrecioCodigo.nomenclador_id.in_(perdedoras_ids))
                .values(nomenclador_id=sobreviviente.id)
            )
            r3 = await db.execute(
                update(MedicoCodigoHabilitado).where(MedicoCodigoHabilitado.nomenclador_id.in_(perdedoras_ids))
                .values(nomenclador_id=sobreviviente.id)
            )
            r4 = await db.execute(
                update(Homologador).where(Homologador.nomenclador_id.in_(perdedoras_ids))
                .values(nomenclador_id=sobreviviente.id)
            )
            r5 = await db.execute(
                update(DetalleFacturacionCMC).where(DetalleFacturacionCMC.nomenclador_id.in_(perdedoras_ids))
                .values(nomenclador_id=sobreviviente.id)
            )
            repunteos["valores"] += r1.rowcount or 0
            repunteos["historial"] += r2.rowcount or 0
            repunteos["habilitaciones_medico"] += r3.rowcount or 0
            repunteos["homologador"] += r4.rowcount or 0
            repunteos["detalle_facturacion"] += r5.rowcount or 0

            for perdedora_id in perdedoras_ids:
                obj = await db.get(NomencladorCMC, perdedora_id)
                await db.delete(obj)
            fusionados += 1
            print(f"  {codigo}: sobrevive id {sobreviviente.id} "
                  f"(obra_social_nro={sobreviviente.obra_social_nro}, activo={sobreviviente.activo}); "
                  f"se borran {perdedoras_ids}")

        await db.commit()

        print(f"\nFusionados: {fusionados}")
        print(f"Repunteos: {repunteos}")
        if ambiguos:
            print(f"\nAMBIGUOS (no tocados, revisar a mano): {len(ambiguos)}")
            for a in ambiguos:
                print(f"  {a}")
        else:
            print("\nSin ambiguos.")

    await engine.dispose()
    print("\nOK")


if __name__ == "__main__":
    asyncio.run(main())
