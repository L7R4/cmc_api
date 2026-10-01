"""Plantilla de especialidades sugeridas de un código + su aplicación a obras sociales.

La plantilla (`nm_plantilla_especialidad_codigo`) es sólo una sugerencia del
catálogo. Recién al aplicarla a una obra social se escribe lo que cuenta:
- las variantes NE por especialidad en `nm_valores` (y la habilitación en
  `nm_valor_especialidad`), o
- el flag "sin restricción" del par (obra social, código).
"""
from __future__ import annotations

import logging
from typing import List

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.catalogs import Especialidad
from app.db.models.nomenclador_cmc import (
    NomencladorCMC,
    NomencladorPlantillaEspecialidad,
    Valor,
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
    """
    # Import local: routes_valores importa de este paquete (evita ciclo de import).
    from app.modules.nomenclador.routes_valores import _buscar_valor_activo, _clonar_valor

    plantilla = await leer_plantilla(db, nom.codigo)
    sin_restriccion = bool(nom.sin_restriccion_especialidad)
    codigo, nom_id = nom.codigo, nom.id

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

            creadas = existentes = 0
            for esp in plantilla:
                if await _buscar_valor_activo(db, os_nro, nom_id, "NE", esp) is not None:
                    existentes += 1
                    continue
                await service.validar_especialidad_habilitada(db, codigo, os_nro, esp)
                await _clonar_valor(
                    db, base, base.vigencia_desde,
                    motivo="replicacion",
                    como_ne_de_especialidad=esp,
                )
                creadas += 1
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
