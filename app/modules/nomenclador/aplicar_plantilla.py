"""Plantilla de especialidades sugeridas de un código + su aplicación a obras sociales.

La plantilla (`nm_plantilla_especialidad_codigo`) es sólo una sugerencia del
catálogo. Recién al aplicarla a una obra social se escribe lo que cuenta:
- las variantes NE por especialidad en `nm_valores` (y la habilitación en
  `nm_valor_especialidad`), o
- el flag "sin restricción" del par (obra social, código).
"""
from __future__ import annotations

import datetime
import logging
from typing import List

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.db.models.catalogs import Especialidad
from app.db.models.nomenclador_cmc import (
    Galeno,
    HistorialPrecioCodigo,
    NomencladorCMC,
    NomencladorPlantillaEspecialidad,
    Valor,
    ValorComponente,
    ValorEspecialidad,
)
from app.modules.nomenclador import service
from app.modules.nomenclador.schemas import (
    AplicarEspecialidadesAplicadaOut,
    AplicarEspecialidadesOmitidaOut,
    AplicarEspecialidadesOut,
)

log = logging.getLogger(__name__)

MOTIVO_SIN_CODIGO = "No existe código"


async def leer_plantilla(db: AsyncSession, codigo: str) -> list[int]:
    filas = (await db.execute(
        select(NomencladorPlantillaEspecialidad.especialidad_id_colegio)
        .where(NomencladorPlantillaEspecialidad.codigo == codigo)
        .order_by(NomencladorPlantillaEspecialidad.especialidad_id_colegio)
    )).scalars().all()
    return list(filas)


async def reemplazar_plantilla(
    db: AsyncSession, codigo: str, especialidad_ids: List[int]
) -> None:
    """Reemplaza por completo la plantilla del código. `ValueError` si alguna
    especialidad no existe en el catálogo. No hace commit."""
    ids = list(dict.fromkeys(especialidad_ids))
    if ids:
        validas = set((await db.execute(
            select(Especialidad.ID_COLEGIO_ESPE).where(Especialidad.ID_COLEGIO_ESPE.in_(ids))
        )).scalars())
        invalidas = set(ids) - validas
        if invalidas:
            raise ValueError(f"Especialidad(es) inexistente(s): {sorted(invalidas)}")
    await db.execute(delete(NomencladorPlantillaEspecialidad).where(
        NomencladorPlantillaEspecialidad.codigo == codigo
    ))
    for eid in ids:
        db.add(NomencladorPlantillaEspecialidad(codigo=codigo, especialidad_id_colegio=eid))
    await db.flush()


async def aplicar_a_obras_sociales(
    db: AsyncSession, nom: NomencladorCMC, obra_social_nros: List[int]
) -> AplicarEspecialidadesOut:
    """Aplica la plantilla guardada de `nom` a cada obra social, una por una (éxito
    parcial: cada una commitea o revierte por separado).

    Por obra social:
    - Si el código no tiene ningún Valor activo → se omite ("No existe código").
    - "Sin restricción" → se fija el flag en el par; no se crean variantes.
    - Si no → una NE por especialidad de la plantilla, clonando componentes y
      vigencia de la primera variante activa del par (NE antes que NN, luego id).
      Las NE que ya existían no se tocan.

    Las variantes de cada obra social se crean EN BLOQUE (`_crear_variantes_en_bloque`):
    un código con 74 especialidades en 66 O.S. son miles de filas, y crearlas de a una
    con `_clonar_valor` (~25 consultas cada una) tardaba minutos contra la base de prod.
    """
    plantilla = await leer_plantilla(db, nom.codigo)
    sin_restriccion = bool(nom.sin_restriccion_especialidad)
    codigo, nom_id = nom.codigo, nom.id
    # Una sola vez para todas las O.S. (antes: una consulta por especialidad y O.S.).
    validas = set((await db.execute(
        select(Especialidad.ID_COLEGIO_ESPE).where(Especialidad.ID_COLEGIO_ESPE.in_(plantilla))
    )).scalars()) if plantilla else set()

    aplicadas: list[AplicarEspecialidadesAplicadaOut] = []
    omitidas: list[AplicarEspecialidadesOmitidaOut] = []

    for os_nro in dict.fromkeys(obra_social_nros):
        try:
            base_candidatas = list((await db.execute(
                select(Valor).where(
                    Valor.obra_social_nro == os_nro,
                    Valor.nomenclador_id == nom_id,
                    Valor.estado == "activo",
                ).order_by(Valor.id)
                # `componentes` es selectin: no traerlos para cada variante existente.
                .options(noload(Valor.componentes))
            )).scalars())
            if not base_candidatas:
                omitidas.append(AplicarEspecialidadesOmitidaOut(
                    obra_social_nro=os_nro, motivo=MOTIVO_SIN_CODIGO))
                continue
            base = next(
                (v for v in base_candidatas if v.origen == "NE"), base_candidatas[0]
            )

            if sin_restriccion:
                await service.fijar_sin_restriccion_par(db, os_nro, codigo, True)
                await db.commit()
                aplicadas.append(AplicarEspecialidadesAplicadaOut(
                    obra_social_nro=os_nro, variantes_creadas=0))
                continue

            ne_existentes = {
                v.especialidad_id_colegio for v in base_candidatas if v.origen == "NE"
            }
            a_crear = [esp for esp in plantilla if esp not in ne_existentes]
            existentes = len(plantilla) - len(a_crear)
            invalida = next((esp for esp in a_crear if esp not in validas), None)
            if invalida is not None:
                raise ValueError(f"La especialidad {invalida} no existe en el catálogo")
            if a_crear:
                await _crear_variantes_en_bloque(db, base, codigo, a_crear)
            creadas = len(a_crear)
            await db.commit()
            aplicadas.append(AplicarEspecialidadesAplicadaOut(
                obra_social_nro=os_nro, variantes_creadas=creadas,
                variantes_existentes=existentes))
        except Exception as e:  # una OS que falla no arrastra a las demás
            await db.rollback()
            log.exception("Aplicar plantilla del código %s a la O.S. %s falló", codigo, os_nro)
            omitidas.append(AplicarEspecialidadesOmitidaOut(
                obra_social_nro=os_nro,
                motivo=str(e) if isinstance(e, ValueError) else "Error inesperado",
            ))

    return AplicarEspecialidadesOut(aplicadas=aplicadas, omitidas=omitidas)


async def _crear_variantes_en_bloque(
    db: AsyncSession, base: Valor, codigo: str, especialidades: List[int]
) -> None:
    """Crea una NE por especialidad clonando `base`, con el mismo resultado que
    `_clonar_valor(..., como_ne_de_especialidad=esp, motivo="replicacion")` +
    `validar_especialidad_habilitada` por cada una, pero con una cantidad fija de
    consultas por obra social (inserts multi-fila) en vez de ~25 por variante.

    Precondición: ninguna de `especialidades` tiene una NE activa en el par (el
    caller ya filtró las existentes). No hace commit.
    """
    os_nro, nom_id, vigencia = base.obra_social_nro, base.nomenclador_id, base.vigencia_desde

    # 1. Habilitación en nm_valor_especialidad (solo las que faltan).
    habilitadas = set((await db.execute(
        select(ValorEspecialidad.especialidad_id_colegio).where(
            ValorEspecialidad.obra_social_nro == os_nro,
            ValorEspecialidad.codigo == codigo,
            ValorEspecialidad.especialidad_id_colegio.in_(especialidades),
        )
    )).scalars())
    faltantes = [esp for esp in especialidades if esp not in habilitadas]
    if faltantes:
        await db.execute(insert(ValorEspecialidad), [
            {"obra_social_nro": os_nro, "codigo": codigo, "especialidad_id_colegio": esp}
            for esp in faltantes
        ])

    # 2. Los valores: mismos metadatos que `_clonar_valor` (incluido no copiar
    #    `por_presupuesto`, igual que el clon de siempre).
    await db.execute(insert(Valor), [
        {
            "obra_social_nro": os_nro, "nomenclador_id": nom_id, "origen": "NE",
            "codigo": base.codigo, "descripcion": base.descripcion, "nivel": base.nivel,
            "complejidad": base.complejidad, "categoria": base.categoria,
            "requiere_autorizacion": base.requiere_autorizacion,
            "especialidad_id_colegio": esp,
            "sin_restriccion_especialidad": base.sin_restriccion_especialidad,
            "cantidad_ayudantes": base.cantidad_ayudantes, "coseguro": base.coseguro,
            "vigencia_desde": vigencia, "vigencia_hasta": None, "estado": "activo",
            "observacion": base.observacion,
        }
        for esp in especialidades
    ])
    # Sin RETURNING en MySQL: se leen los ids recién creados. Son las únicas NE
    # activas de esas especialidades en el par (precondición).
    nuevos = dict((await db.execute(
        select(Valor.especialidad_id_colegio, Valor.id).where(
            Valor.obra_social_nro == os_nro, Valor.nomenclador_id == nom_id,
            Valor.origen == "NE", Valor.estado == "activo",
            Valor.especialidad_id_colegio.in_(especialidades),
        )
    )).all())

    # 3. Componentes: copia de los activos de la base en cada valor nuevo.
    comps_base = list((await db.execute(
        select(ValorComponente).where(
            ValorComponente.valor_id == base.id, ValorComponente.activo == True,
        )
    )).scalars())
    if comps_base:
        await db.execute(insert(ValorComponente), [
            {
                "valor_id": valor_id, "concepto": c.concepto, "galeno_id": c.galeno_id,
                "cantidad": c.cantidad, "valor_unitario": c.valor_unitario,
                "orden": c.orden, "observacion": c.observacion,
            }
            for valor_id in nuevos.values() for c in comps_base
        ])
    comps_por_valor: dict[int, list] = {vid: [] for vid in nuevos.values()}
    for c in (await db.execute(
        select(
            ValorComponente.id, ValorComponente.valor_id, ValorComponente.concepto,
            ValorComponente.galeno_id, ValorComponente.cantidad, ValorComponente.valor_unitario,
        ).where(
            ValorComponente.valor_id.in_(list(nuevos.values())),
            ValorComponente.activo == True,
        ).order_by(ValorComponente.valor_id, ValorComponente.orden)
    )).all():
        comps_por_valor[c.valor_id].append(c)

    # 4. Historial (Regla B sin fecha de corte, como `regenerar_historial_por_valores`):
    #    actualiza la fila de esa vigencia si ya existe; si no, inserta una nueva.
    galenos = {
        gid: await db.get(Galeno, gid)
        for gid in {c.galeno_id for c in comps_base if c.galeno_id is not None}
    }
    previas = {
        h.especialidad_id_colegio: h for h in (await db.execute(
            select(HistorialPrecioCodigo).where(
                HistorialPrecioCodigo.nomenclador_id == nom_id,
                HistorialPrecioCodigo.obra_social_nro == os_nro,
                HistorialPrecioCodigo.origen == "NE",
                HistorialPrecioCodigo.especialidad_id_colegio.in_(especialidades),
                HistorialPrecioCodigo.vigencia_desde == vigencia,
            )
        )).scalars()
    }
    ahora = datetime.datetime.utcnow()
    filas_historial = []
    for esp, valor_id in nuevos.items():
        precio, snapshot = service.precio_y_snapshot(comps_por_valor[valor_id], galenos)
        previa = previas.get(esp)
        if previa is not None:
            previa.precio_total = precio
            previa.componentes_snapshot = snapshot
            previa.motivo_cambio = "replicacion"
            previa.vigencia_hasta = None
            previa.valores_id = valor_id
            previa.fecha_cambio = ahora
            continue
        filas_historial.append({
            "nomenclador_id": nom_id, "obra_social_nro": os_nro, "origen": "NE",
            "especialidad_id_colegio": esp, "vigencia_desde": vigencia,
            "vigencia_hasta": None, "precio_total": precio, "valores_id": valor_id,
            "componentes_snapshot": snapshot, "motivo_cambio": "replicacion",
            "referencia_cambio_id": valor_id, "fecha_cambio": ahora,
        })
    if filas_historial:
        await db.execute(insert(HistorialPrecioCodigo), filas_historial)
    await db.flush()
