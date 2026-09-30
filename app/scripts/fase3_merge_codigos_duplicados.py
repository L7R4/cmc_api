"""
Fase 3 de la reestructura del nomenclador — paso previo obligatorio al DROP de
columnas: fusiona los códigos que todavía tienen más de una fila en
nm_nomenclador (remanente de cuando `obra_social_nro` distinguía una versión
"propia de una OS" de la "compartida" del Colegio — ver obstáculo 7 del plan).

Para cada código con >1 fila, elige una sobreviviente. Producción sigue en uso
mientras corre esta migración, así que además de las 3 colisiones históricas
(obstáculo 7) puede haber duplicados nuevos y perfectamente legítimos: un
código creado independientemente para varias OS bajo el diseño viejo (cada
uno con su propio `obra_social_nro`, todos activos). Como a partir de la fase
3 `nm_nomenclador` no guarda nada específico de OS, cuál fila sobrevive es
indistinto siempre que categoría/complejidad coincidan — lo único
verdaderamente ambiguo es que difieran:
  1. Si categoría o complejidad difieren entre las filas del grupo, es
     ambiguo de verdad — no se toca, queda listado para revisar a mano.
  2. Si hay EXACTAMENTE UNA fila activa, esa gana (es la que de verdad se
     está usando).
  3. Si hay una fila compartida (obra_social_nro IS NULL) entre las activas,
     esa gana.
  4. Si no, la fila activa de id más chico (la más vieja en uso).
  5. Si no hay ninguna activa, la compartida entre todas, o si tampoco, la de
     id más chico.

Todo lo que colgaba de las filas perdedoras (Valor, HistorialPrecioCodigo,
MedicoCodigoHabilitado, Homologador, detalle_facturacion) se repunta a la
sobreviviente, y las filas perdedoras se borran. `nm_valor_especialidad` y
`nm_nomenclador_descripcion_legacy` no necesitan tocarse: ya cuelgan del string
`codigo`, no de `nomenclador_id` (por eso nunca hizo falta este merge en las
fases 1 y 2 — recién ahora, para poder crear UNIQUE(codigo), es obligatorio).

`nm_nomenclador_especialidad` (la tabla vieja, reemplazada por
`nm_valor_especialidad` desde la fase 1) todavía tiene FK a `nomenclador_id`
mientras no corra el DDL de la fase 3 — sus filas para las perdedoras se
borran sin repuntear: la tabla entera se dropea a continuación y su dato ya
está superado.

Correr en producción DESPUÉS del backup de las tablas nm_* y ANTES del DDL de
`scripts/nomenclador_fase3_<fecha>.sql` (que agrega UNIQUE(codigo) y falla si
queda algún duplicado sin fusionar).

Ejecutar:
  docker exec fastapi python -m app.scripts.fase3_merge_codigos_duplicados
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

from sqlalchemy import bindparam, text, update

from app.db.database import AsyncSessionLocal, engine
from app.db.models import (
    DetalleFacturacionCMC, Homologador, HistorialPrecioCodigo,
    MedicoCodigoHabilitado, NomencladorCMC, Valor,
)

# Producción ya corre el código de la fase 3 (modelo sin `obra_social_nro`)
# pero la DB todavía tiene el schema viejo con esa columna — por eso esta
# lectura inicial va por SQL crudo en vez de por el ORM.


async def main() -> None:
    async with AsyncSessionLocal() as db:
        filas = (await db.execute(text(
            "SELECT id, codigo, obra_social_nro, activo, categoria, complejidad "
            "FROM nm_nomenclador ORDER BY codigo, id"
        ))).all()

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
            distintas = {(f.categoria, f.complejidad) for f in fs}
            if len(distintas) > 1:
                ambiguos.append({
                    "codigo": codigo,
                    "motivo": "categoria/complejidad distintos",
                    "filas": [(f.id, f.categoria, f.complejidad) for f in fs],
                })
                continue

            activas = [f for f in fs if f.activo]
            if len(activas) == 1:
                sobreviviente = activas[0]
            elif activas:
                compartidas_activas = [f for f in activas if f.obra_social_nro is None]
                sobreviviente = compartidas_activas[0] if compartidas_activas else min(activas, key=lambda f: f.id)
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

            await db.execute(
                text("DELETE FROM nm_nomenclador_especialidad WHERE nomenclador_id IN :ids")
                .bindparams(bindparam("ids", expanding=True)),
                {"ids": perdedoras_ids},
            )

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
