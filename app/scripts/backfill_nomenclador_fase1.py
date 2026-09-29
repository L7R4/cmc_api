"""
Backfill de la fase 1 de la reestructura del nomenclador (ver plan de migración).

Puebla las tablas nuevas (nm_nomenclador_nacional, nm_valor_especialidad,
nm_nomenclador_descripcion_legacy) y las dos columnas nuevas
(nm_nomenclador.nomenclador_nacional_id, nm_valores.sin_restriccion_especialidad) a
partir del estado ACTUAL de nm_nomenclador / nm_nomenclador_especialidad /
nm_valores. No borra ni modifica ninguna columna vieja — son puramente aditivas,
el resto del sistema sigue funcionando exactamente igual hasta que el código de la
fase 2 empiece a leerlas.

Idempotente: cada corrida vacía y recarga las 3 tablas nuevas desde cero y vuelve a
calcular las dos columnas agregadas. Se puede correr las veces que haga falta.

Ejecutar (con el proyecto en /app dentro del contenedor):
  docker exec fastapi python app/scripts/backfill_nomenclador_fase1.py
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import AsyncSessionLocal, engine
from app.db.models import (
    DetalleFacturacionCMC, NomencladorCMC, NomencladorDescripcionLegacy,
    NomencladorEspecialidad, NomencladorNacional, Valor, ValorEspecialidad,
)


async def _elegir_representante(
    db: AsyncSession, ids: list[int]
) -> tuple[int, dict[int, tuple[int, int]]]:
    """Entre varias filas de NomencladorCMC que van a compartir una misma fila NN
    (mismo codigo_nacional, o mismo codigo propio colisionando), elige cuál presta
    sus datos (descripcion/unidades/categoria/complejidad) a la fila NN nueva.

    Gana la que tenga más Valores activos (la que de verdad se está usando);
    empate → más filas en detalle_facturacion (historial real de facturación);
    empate → el id más chico (la más antigua, para que el resultado sea estable
    entre corridas).
    """
    counts: dict[int, tuple[int, int]] = {}
    for nid in ids:
        valores_activos = (await db.execute(
            select(func.count()).select_from(Valor)
            .where(Valor.nomenclador_id == nid, Valor.estado == "activo")
        )).scalar_one()
        df = (await db.execute(
            select(func.count()).select_from(DetalleFacturacionCMC)
            .where(DetalleFacturacionCMC.nomenclador_id == nid)
        )).scalar_one()
        counts[nid] = (valores_activos, df)
    ganador = min(ids, key=lambda nid: (-counts[nid][0], -counts[nid][1], nid))
    return ganador, counts


async def backfill_legacy_descripciones(db: AsyncSession) -> int:
    await db.execute(delete(NomencladorDescripcionLegacy))
    filas = (await db.execute(
        select(
            NomencladorCMC.id, NomencladorCMC.obra_social_nro,
            NomencladorCMC.codigo, NomencladorCMC.descripcion,
        )
    )).all()
    for nid, os_nro, codigo, descripcion in filas:
        db.add(NomencladorDescripcionLegacy(
            nomenclador_id=nid, obra_social_nro=os_nro, codigo=codigo,
            descripcion=descripcion or "",
        ))
    await db.flush()
    return len(filas)


async def backfill_nomenclador_nacional(db: AsyncSession) -> dict:
    """Agrupa nm_nomenclador por codigo_nacional (los que lo tienen) y por su propio
    codigo (los 158 que tienen unidades_* pero no codigo_nacional, para no perder su
    elegibilidad de generación NN — ver obstáculo 6 del plan). Una fila NN por grupo."""
    await db.execute(update(NomencladorCMC).values(nomenclador_nacional_id=None))
    await db.execute(delete(NomencladorNacional))
    await db.flush()

    filas = (await db.execute(
        select(
            NomencladorCMC.id, NomencladorCMC.codigo, NomencladorCMC.codigo_nacional,
            NomencladorCMC.descripcion, NomencladorCMC.categoria, NomencladorCMC.complejidad,
            NomencladorCMC.unidades_honorarios, NomencladorCMC.unidades_ayudante,
            NomencladorCMC.unidades_gastos,
        )
        .where(
            NomencladorCMC.codigo_nacional.is_not(None)
            | NomencladorCMC.unidades_honorarios.is_not(None)
            | NomencladorCMC.unidades_ayudante.is_not(None)
            | NomencladorCMC.unidades_gastos.is_not(None)
        )
        .order_by(NomencladorCMC.id)
    )).all()

    grupos: dict[str, list] = defaultdict(list)
    for fila in filas:
        clave = fila.codigo_nacional or f"__sin_cn__{fila.codigo}"
        grupos[clave].append(fila)

    conflictos: list[dict] = []
    sin_codigo_nacional: list[str] = []
    codigos_nn_usados: set[str] = set()
    creados = 0
    vinculados = 0

    for clave, fs in grupos.items():
        es_sintetico = clave.startswith("__sin_cn__")
        codigo_nn = fs[0].codigo if es_sintetico else clave
        if es_sintetico:
            sin_codigo_nacional.append(fs[0].codigo)

        if codigo_nn in codigos_nn_usados:
            # El código elegido para esta fila NN ya lo usó otro grupo antes (choca un
            # codigo_nacional real contra el codigo propio de otro código sin
            # codigo_nacional, o dos códigos propios idénticos entre sí). Se salta:
            # queda sin vincular, listado para revisar a mano con el reporte.
            conflictos.append({
                "codigo_nn": codigo_nn, "motivo": "codigo_ya_usado_por_otro_grupo",
                "ids": [f.id for f in fs],
            })
            continue
        codigos_nn_usados.add(codigo_nn)

        if len(fs) > 1:
            ganador_id, counts = await _elegir_representante(db, [f.id for f in fs])
            representante = next(f for f in fs if f.id == ganador_id)
            conflictos.append({
                "codigo_nn": codigo_nn, "motivo": "codigo_nacional_compartido",
                "ganador": ganador_id,
                "candidatos": {f.id: counts[f.id] for f in fs},
            })
        else:
            representante = fs[0]

        nn = NomencladorNacional(
            codigo=codigo_nn,
            descripcion=representante.descripcion,
            unidades_honorarios=representante.unidades_honorarios,
            unidades_ayudante=representante.unidades_ayudante,
            unidades_gastos=representante.unidades_gastos,
            categoria=representante.categoria,
            complejidad=representante.complejidad,
            activo=True,
        )
        db.add(nn)
        await db.flush()
        creados += 1

        ids_a_vincular = [f.id for f in fs]
        await db.execute(
            update(NomencladorCMC).where(NomencladorCMC.id.in_(ids_a_vincular))
            .values(nomenclador_nacional_id=nn.id)
        )
        vinculados += len(ids_a_vincular)

    return {
        "nn_creados": creados,
        "colegio_vinculados": vinculados,
        "conflictos": conflictos,
        "sin_codigo_nacional": sin_codigo_nacional,
    }


async def backfill_valores(db: AsyncSession) -> tuple[int, int]:
    """descripcion: solo rellena los Valor activos que todavía no tienen la propia
    (no pisa los 2286 NE que ya la traían cargada). sin_restriccion_especialidad: se
    fija en todos los activos desde el default del catálogo (hoy 0 en el 100% de los
    casos, pero no asume eso — lee la columna real)."""
    r1 = await db.execute(text(
        """
        UPDATE nm_valores v
        JOIN nm_nomenclador n ON n.id = v.nomenclador_id
        SET v.descripcion = n.descripcion
        WHERE v.estado = 'activo' AND v.descripcion IS NULL
        """
    ))
    r2 = await db.execute(text(
        """
        UPDATE nm_valores v
        JOIN nm_nomenclador n ON n.id = v.nomenclador_id
        SET v.sin_restriccion_especialidad = n.sin_restriccion_especialidad
        WHERE v.estado = 'activo'
        """
    ))
    return r1.rowcount, r2.rowcount


async def backfill_valor_especialidad(db: AsyncSession) -> int:
    """Para cada par (obra_social_nro, codigo) con Valor activo, copia las
    especialidades efectivas de HOY a la tabla nueva.

    Resuelve la precedencia (OS propia > compartida del Colegio) EN MEMORIA en vez
    de llamar a `especialidades_habilitadas_de` una vez por par: contra una base
    remota (producción, latencia de red) un round-trip por cada uno de los
    ~cientos de miles de pares es prohibitivo y deja la transacción abierta
    demasiado tiempo. Acá se trae UNA sola vez todo `nm_nomenclador_especialidad`
    y se replica la misma regla que usa esa función (ver service.py:
    `especialidades_habilitadas_de` / `_nivel_pertenencia_especialidad`): si el
    par (nomenclador_id, obra_social_nro) tiene alguna regla propia de esa OS,
    ganan esas; si no, valen las compartidas del Colegio (obra_social_nro NULL)
    de ese código."""
    await db.execute(delete(ValorEspecialidad))
    await db.flush()

    reglas = (await db.execute(
        select(
            NomencladorEspecialidad.nomenclador_id,
            NomencladorEspecialidad.obra_social_nro,
            NomencladorEspecialidad.especialidad_id_colegio,
        ).where(NomencladorEspecialidad.activo.is_(True))
    )).all()

    propias: dict[tuple[int, int], set[int]] = defaultdict(set)
    compartidas: dict[int, set[int]] = defaultdict(set)
    for nomenclador_id, os_nro, esp_id in reglas:
        if os_nro is None:
            compartidas[nomenclador_id].add(esp_id)
        else:
            propias[(nomenclador_id, os_nro)].add(esp_id)

    pares = (await db.execute(
        select(Valor.obra_social_nro, Valor.nomenclador_id, Valor.codigo)
        .where(Valor.estado == "activo")
        .distinct()
    )).all()

    filas: list[dict] = []
    vistos: set[tuple[int, str, int]] = set()
    for os_nro, nomenclador_id, codigo in pares:
        especialidades = propias.get((nomenclador_id, os_nro))
        if especialidades is None:
            especialidades = compartidas.get(nomenclador_id, set())
        for esp_id in especialidades:
            clave = (os_nro, codigo, esp_id)
            if clave in vistos:
                continue
            vistos.add(clave)
            filas.append({
                "obra_social_nro": os_nro, "codigo": codigo,
                "especialidad_id_colegio": esp_id,
            })

    CHUNK = 5000
    for i in range(0, len(filas), CHUNK):
        await db.execute(insert(ValorEspecialidad), filas[i:i + CHUNK])
    await db.flush()
    return len(filas)


async def main() -> None:
    async with AsyncSessionLocal() as db:
        try:
            n_legacy = await backfill_legacy_descripciones(db)
            print(f"[1/4] nm_nomenclador_descripcion_legacy: {n_legacy} filas")

            resumen_nn = await backfill_nomenclador_nacional(db)
            print(
                f"[2/4] nm_nomenclador_nacional: {resumen_nn['nn_creados']} filas creadas, "
                f"{resumen_nn['colegio_vinculados']} códigos del Colegio vinculados"
            )
            print(f"       sin codigo_nacional (usaron su propio código): "
                  f"{len(resumen_nn['sin_codigo_nacional'])}")
            print(f"       conflictos (codigo_nacional compartido o choque de código): "
                  f"{len(resumen_nn['conflictos'])}")

            n_desc, n_restr = await backfill_valores(db)
            print(f"[3/4] nm_valores.descripcion completadas: {n_desc}; "
                  f"sin_restriccion_especialidad actualizadas: {n_restr}")

            n_esp = await backfill_valor_especialidad(db)
            print(f"[4/4] nm_valor_especialidad: {n_esp} filas")

            await db.commit()
            print("\nCOMMIT OK")

            if resumen_nn["conflictos"]:
                print("\n--- Conflictos NN (revisar con scripts/reporte_migracion_nomenclador.py) ---")
                for c in resumen_nn["conflictos"][:20]:
                    print(c)
                if len(resumen_nn["conflictos"]) > 20:
                    print(f"... y {len(resumen_nn['conflictos']) - 20} más")
        except Exception:
            await db.rollback()
            raise
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
