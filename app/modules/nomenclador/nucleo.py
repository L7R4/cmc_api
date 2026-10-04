"""Edición del "núcleo" de un código en una obra social.

El núcleo es lo que vale PARA TODAS las variantes por especialidad de un
(obra social, código): metadatos, ecuación (vigencia + valores + coseguro) y el
conjunto de especialidades. Editar una variante suelta sólo toca su ecuación
(`POST /valores_nm/{id}/actualizar`).

Todo corre en UNA transacción del caller: si algo falla no queda un par a medias.
"""
from __future__ import annotations

import datetime
from typing import List

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.nomenclador_cmc import NomencladorCMC, Valor, ValorEspecialidad
from app.modules.nomenclador import service
from app.modules.nomenclador.schemas import ValorNucleoUpdate


async def _ne_activas(db: AsyncSession, os_nro: int, nom_id: int) -> List[Valor]:
    return list((await db.execute(
        select(Valor).where(
            Valor.obra_social_nro == os_nro,
            Valor.nomenclador_id == nom_id,
            Valor.origen == "NE",
            Valor.estado == "activo",
        ).order_by(Valor.id)
    )).scalars())


async def _nn_activas(db: AsyncSession, os_nro: int, nom_id: int) -> List[Valor]:
    return list((await db.execute(
        select(Valor).where(
            Valor.obra_social_nro == os_nro,
            Valor.nomenclador_id == nom_id,
            Valor.origen == "NN",
            Valor.estado == "activo",
        ).order_by(Valor.id)
    )).scalars())


async def _cerrar(db: AsyncSession, valor: Valor) -> None:
    """Baja con efecto inmediato — igual que `DELETE /valores_nm/{id}`."""
    from app.modules.nomenclador.routes_valores import _cerrar_valor

    ayer = datetime.date.today() - datetime.timedelta(days=1)
    _cerrar_valor(valor, ayer)
    await service.cerrar_historial_de_valor(valor.id, ayer, db)


async def actualizar_nucleo(
    db: AsyncSession, os_nro: int, nom_id: int, body: ValorNucleoUpdate
) -> List[Valor]:
    """Aplica `body` al núcleo. Devuelve las variantes activas del par al terminar.
    Los datos del par también quedan en el alta del código en la O.S. (etapa 3).
    No hace commit."""
    from app.modules.nomenclador import alta_os

    valores = await _actualizar_nucleo(db, os_nro, nom_id, body)
    if valores:
        base = next((v for v in valores if v.origen == "NE"), valores[0])
        await alta_os.sincronizar_par_desde_valor(db, base)
    return valores


async def _actualizar_nucleo(
    db: AsyncSession, os_nro: int, nom_id: int, body: ValorNucleoUpdate
) -> List[Valor]:
    from app.modules.nomenclador.routes_valores import (
        _actualizar_valor_core,
        _clonar_valor,
        _forzar_ayudantes_honorarios_individuales,
        nivel_fijado_por_galeno,
    )

    nom = await db.get(NomencladorCMC, nom_id)
    if not nom:
        raise HTTPException(404, "Código de nomenclador no encontrado")
    ne = await _ne_activas(db, os_nro, nom_id)
    if body.origen == "NN" or not ne:
        nn = await _nn_activas(db, os_nro, nom_id)
        if not nn:
            raise HTTPException(409, "El código no tiene valores activos en esta obra social")
        return await _actualizar_nucleo_nn(db, nom, nn, body)

    # 1) Metadatos → todas las variantes activas del par (la descripción también a NN).
    meta = {
        k: getattr(body, k)
        for k in ("nivel", "complejidad", "cantidad_ayudantes", "observacion")
        if getattr(body, k) is not None
    }
    for v in ne:
        for k, val in meta.items():
            # Con galeno nivelado el nivel es el del galeno (cambia con el galeno).
            if k == "nivel" and await nivel_fijado_por_galeno(db, v.id) is not None:
                continue
            setattr(v, k, val)
        v.cantidad_ayudantes = _forzar_ayudantes_honorarios_individuales(
            v.categoria, nom, v.cantidad_ayudantes
        )
    if body.descripcion is not None:
        for v in await service.variantes_del_par(db, os_nro, nom.codigo):
            v.descripcion = body.descripcion.strip() or None
    await db.flush()

    # 2) Ecuación → rota TODAS las NE activas.
    if body.ecuacion is not None:
        ecu = body.ecuacion.model_copy(update={"aplicar_a_variantes": True})
        await _actualizar_valor_core(db, ne[0].id, ecu)
        ne = await _ne_activas(db, os_nro, nom_id)

    # 3) Especialidades (salvo que el caller sólo rote precios).
    if body.tocar_especialidades:
        base = ne[0]
        hay_sin_especialidad = any(v.especialidad_id_colegio is None for v in ne)
        deseadas = list(dict.fromkeys(body.especialidades))

        if body.sin_restriccion_especialidad:
            if not hay_sin_especialidad:
                await service.fijar_sin_restriccion_par(db, os_nro, nom.codigo, True)
                for v in ne:
                    await _cerrar(db, v)
                await db.flush()
                await _clonar_valor(
                    db, base, base.vigencia_desde, motivo="valores_estructura",
                    como_ne_sin_especialidad=True, sin_restriccion=True,
                )
            await db.execute(delete(ValorEspecialidad).where(
                ValorEspecialidad.obra_social_nro == os_nro,
                ValorEspecialidad.codigo == nom.codigo,
            ))
        else:
            if not deseadas:
                raise HTTPException(
                    422, "Elegí al menos una especialidad o marcá 'Sin restricción por especialidad'."
                )
            if hay_sin_especialidad:
                sin_esp = next(v for v in ne if v.especialidad_id_colegio is None)
                for v in ne:
                    await _cerrar(db, v)
                await db.flush()
                await service.fijar_sin_restriccion_par(db, os_nro, nom.codigo, False)
                for esp in deseadas:
                    await _validar_y_habilitar(db, nom.codigo, os_nro, esp)
                    await _clonar_valor(
                        db, sin_esp, sin_esp.vigencia_desde, motivo="valores_estructura",
                        como_ne_de_especialidad=esp, sin_restriccion=False,
                    )
            else:
                actuales = {v.especialidad_id_colegio for v in ne}
                for esp in deseadas:
                    if esp not in actuales:
                        await _validar_y_habilitar(db, nom.codigo, os_nro, esp)
                        await _clonar_valor(
                            db, base, base.vigencia_desde, motivo="valores_estructura",
                            como_ne_de_especialidad=esp,
                        )
                for v in ne:
                    if v.especialidad_id_colegio not in deseadas:
                        await _cerrar(db, v)
            await db.flush()
            try:
                await service.reemplazar_especialidades(db, os_nro, nom.codigo, deseadas)
            except ValueError as e:
                raise HTTPException(409, str(e))

    await db.flush()
    return list((await db.execute(
        select(Valor).where(
            Valor.obra_social_nro == os_nro,
            Valor.nomenclador_id == nom_id,
            Valor.estado == "activo",
        ).order_by(Valor.id)
    )).scalars())


async def _validar_y_habilitar(db: AsyncSession, codigo: str, os_nro: int, esp: int) -> None:
    try:
        await service.validar_especialidad_habilitada(db, codigo, os_nro, esp)
    except ValueError as e:
        raise HTTPException(409, str(e))


async def _actualizar_nucleo_nn(
    db: AsyncSession, nom: NomencladorCMC, nn: List[Valor], body: ValorNucleoUpdate
) -> List[Valor]:
    """Núcleo de un código que en la obra social sólo tiene fila NN: una sola fila
    (mismo valor para cualquier especialidad), pero con la misma gestión de
    especialidades que una NE — la lista de habilitadas y "sin restricción" son
    datos del par (`nm_valor_especialidad` / `Valor.sin_restriccion_especialidad`)."""
    from app.modules.nomenclador.routes_valores import (
        _actualizar_valor_core,
        _forzar_ayudantes_honorarios_individuales,
        nivel_fijado_por_galeno,
    )

    os_nro, nom_id = nn[0].obra_social_nro, nom.id

    meta = {
        k: getattr(body, k)
        for k in ("nivel", "complejidad", "cantidad_ayudantes", "observacion")
        if getattr(body, k) is not None
    }
    for v in nn:
        for k, val in meta.items():
            # Con galeno nivelado el nivel es el del galeno (cambia con el galeno).
            if k == "nivel" and await nivel_fijado_por_galeno(db, v.id) is not None:
                continue
            setattr(v, k, val)
        v.cantidad_ayudantes = _forzar_ayudantes_honorarios_individuales(
            v.categoria, nom, v.cantidad_ayudantes
        )
    if body.descripcion is not None:
        for v in await service.variantes_del_par(db, os_nro, nom.codigo):
            v.descripcion = body.descripcion.strip() or None
    await db.flush()

    if body.ecuacion is not None:
        await _actualizar_valor_core(db, nn[0].id, body.ecuacion)

    if not body.tocar_especialidades:
        pass
    elif body.sin_restriccion_especialidad:
        await service.fijar_sin_restriccion_par(db, os_nro, nom.codigo, True)
        await db.execute(delete(ValorEspecialidad).where(
            ValorEspecialidad.obra_social_nro == os_nro,
            ValorEspecialidad.codigo == nom.codigo,
        ))
    else:
        deseadas = list(dict.fromkeys(body.especialidades))
        if not deseadas:
            raise HTTPException(
                422, "Elegí al menos una especialidad o marcá 'Sin restricción por especialidad'."
            )
        try:
            await service.fijar_sin_restriccion_par(db, os_nro, nom.codigo, False)
            await service.reemplazar_especialidades(db, os_nro, nom.codigo, deseadas)
        except ValueError as e:
            raise HTTPException(409, str(e))

    await db.flush()
    return list((await db.execute(
        select(Valor).where(
            Valor.obra_social_nro == os_nro,
            Valor.nomenclador_id == nom_id,
            Valor.estado == "activo",
        ).order_by(Valor.id)
    )).scalars())
