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
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.db.models.catalogs import Especialidad
from app.db.models.nomenclador_cmc import (
    HistorialPrecioCodigo,
    NomencladorCMC,
    NomencladorNacional,
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
            raise ValueError(service.mensaje_especialidades_inexistentes(invalidas))
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
                raise ValueError(service.mensaje_especialidades_inexistentes([invalida]))
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


MOTIVO_PAR_CON_RESTRICCION = (
    "El código ya está cargado en esta obra social por especialidad: no se agrega "
    "el valor sin especialidad para no tocar lo existente"
)


def _componentes_cero() -> list[dict]:
    """Honorarios, Gastos y Ayudante como fijos en $0 (mismos 3 conceptos que lleva
    todo Valor; ver `routes_valores._componentes_presupuesto_cero`)."""
    return [
        {"concepto": concepto, "galeno_id": None, "cantidad": Decimal("0"),
         "valor_unitario": Decimal("0"), "orden": orden, "observacion": None}
        for orden, concepto in enumerate(("Honorarios", "Gastos", "Ayudante"))
    ]


async def alta_ne_en_cero(
    db: AsyncSession,
    nom: NomencladorCMC,
    obra_social_nros: List[int],
    vigencia_desde: datetime.date,
    cantidad_ayudantes: Optional[int],
) -> dict:
    """Herramienta "Agregar código a obras sociales": da de alta `nom` como NE en $0
    en cada obra social, una variante por especialidad de su plantilla — o una sola
    sin especialidad si el código es "sin restricción" en el catálogo. Lo que ya
    existe NO se toca: las variantes NE activas se informan y se saltean.

    Cada obra social va en su propio savepoint (una que falla no arrastra a las
    demás). No hace commit: el caller decide (commit o rollback para la vista previa).

    Ojo con el precio: un NE gana sobre el NN al cotizar (`lookup_precio`), así que
    si la OS ya tiene el código en NN con precio, desde la vigencia del NE en $0 los
    médicos de esas especialidades pasarían a cotizar $0. Se informa en
    `nn_con_precio` para que la pantalla lo advierta.
    """
    sin_restriccion = bool(nom.sin_restriccion_especialidad)
    plantilla = [] if sin_restriccion else await leer_plantilla(db, nom.codigo)
    if not sin_restriccion and not plantilla:
        raise ValueError(
            f"El código {nom.codigo} no tiene especialidades en su plantilla: cargalas "
            "primero en la ficha del código."
        )

    aplicables = [None] if sin_restriccion else plantilla
    resultados: list[dict] = []
    for os_nro in dict.fromkeys(obra_social_nros):
        try:
            async with db.begin_nested():
                resultados.append(await _alta_ne_en_cero_os(
                    db, nom, os_nro, aplicables, sin_restriccion, vigencia_desde,
                    cantidad_ayudantes,
                ))
        except Exception:
            log.exception("Alta NE en $0 del código %s en la O.S. %s falló", nom.codigo, os_nro)
            resultados.append({
                "obra_social_nro": os_nro, "estado": "error", "creadas": [],
                "existentes": [], "motivo": "Error inesperado", "nn_con_precio": False,
            })
    return {"plantilla": plantilla, "sin_restriccion": sin_restriccion, "resultados": resultados}


async def _descripcion_catalogo(db: AsyncSession, nom: NomencladorCMC) -> Optional[str]:
    """La del catálogo del Colegio; si está vacía, la del Nomenclador Nacional
    vinculado (como el alta de los NN). None si no hay ninguna."""
    if nom.descripcion and nom.descripcion.strip():
        return nom.descripcion.strip()
    if nom.nomenclador_nacional_id is not None:
        nn = await db.get(NomencladorNacional, nom.nomenclador_nacional_id)
        if nn is not None and nn.descripcion and nn.descripcion.strip():
            return nn.descripcion.strip()
    return None


async def _alta_ne_en_cero_os(
    db: AsyncSession,
    nom: NomencladorCMC,
    os_nro: int,
    aplicables: list[Optional[int]],
    sin_restriccion: bool,
    vigencia_desde: datetime.date,
    cantidad_ayudantes: Optional[int],
) -> dict:
    activos = list((await db.execute(
        select(Valor).where(
            Valor.obra_social_nro == os_nro,
            Valor.nomenclador_id == nom.id,
            Valor.estado == "activo",
        ).options(noload(Valor.componentes))
    )).scalars())
    ne_existentes = {v.especialidad_id_colegio for v in activos if v.origen == "NE"}
    # "Sin restricción" es dato del PAR: las filas nuevas heredan lo que ya tenga.
    par_sin_restriccion = any(v.sin_restriccion_especialidad for v in activos)

    nn_ids = [v.id for v in activos if v.origen == "NN"]
    nn_con_precio = bool(nn_ids) and (await db.execute(
        select(HistorialPrecioCodigo.id).where(
            HistorialPrecioCodigo.valores_id.in_(nn_ids),
            HistorialPrecioCodigo.vigencia_hasta.is_(None),
            HistorialPrecioCodigo.precio_total > 0,
        ).limit(1)
    )).scalar_one_or_none() is not None

    # La descripción es dato del PAR: si la OS ya tiene el código con la suya (la del
    # convenio), las variantes nuevas la heredan; si no, la del catálogo.
    descripcion = next(
        (v.descripcion for v in activos if v.descripcion and v.descripcion.strip()),
        await _descripcion_catalogo(db, nom),
    )

    a_crear = [esp for esp in aplicables if esp not in ne_existentes]
    existentes = [esp for esp in aplicables if esp in ne_existentes]
    base = {"obra_social_nro": os_nro, "existentes": existentes, "nn_con_precio": nn_con_precio}

    # Un NE sin especialidad solo vale en un par sin restricción; si el código ya
    # está en la OS por especialidad, marcarlo sin restricción tocaría lo existente.
    if sin_restriccion and a_crear and activos and not par_sin_restriccion:
        return {**base, "estado": "omitida", "creadas": [], "motivo": MOTIVO_PAR_CON_RESTRICCION}
    if not a_crear:
        return {**base, "estado": "sin_cambios", "creadas": [], "motivo": None}

    habilitar = [esp for esp in a_crear if esp is not None]
    if habilitar:
        habilitadas = set((await db.execute(
            select(ValorEspecialidad.especialidad_id_colegio).where(
                ValorEspecialidad.obra_social_nro == os_nro,
                ValorEspecialidad.codigo == nom.codigo,
                ValorEspecialidad.especialidad_id_colegio.in_(habilitar),
            )
        )).scalars())
        faltantes = [esp for esp in habilitar if esp not in habilitadas]
        if faltantes:
            await db.execute(insert(ValorEspecialidad), [
                {"obra_social_nro": os_nro, "codigo": nom.codigo, "especialidad_id_colegio": esp}
                for esp in faltantes
            ])

    await service.persistir_valores_en_bloque(db, [
        (
            {
                "obra_social_nro": os_nro, "nomenclador_id": nom.id, "origen": "NE",
                "codigo": nom.codigo, "descripcion": descripcion, "nivel": None,
                "complejidad": None, "categoria": None, "requiere_autorizacion": None,
                "especialidad_id_colegio": esp,
                "sin_restriccion_especialidad": sin_restriccion or par_sin_restriccion,
                "por_presupuesto": False, "cantidad_ayudantes": cantidad_ayudantes,
                "coseguro": Decimal("0"), "vigencia_desde": vigencia_desde,
                "observacion": None,
            },
            _componentes_cero(),
        )
        for esp in a_crear
    ], motivo="carga_inicial")
    return {**base, "estado": "creada", "creadas": a_crear, "motivo": None}


async def _crear_variantes_en_bloque(
    db: AsyncSession, base: Valor, codigo: str, especialidades: List[int]
) -> None:
    """Crea una NE por especialidad clonando `base`, con el mismo resultado que
    `_clonar_valor(..., como_ne_de_especialidad=esp, motivo="replicacion")` +
    `validar_especialidad_habilitada` por cada una, pero con una cantidad fija de
    consultas por obra social en vez de ~25 por variante. El alta pasa por el punto
    único en bloque (`service.persistir_valores_en_bloque`: valor + componentes +
    historial juntos).

    Precondición: ninguna de `especialidades` tiene una NE activa en el par (el
    caller ya filtró las existentes). No hace commit.
    """
    os_nro = base.obra_social_nro

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

    # 2. Valores + componentes + historial. Mismos metadatos que `_clonar_valor`
    #    (incluido no copiar `por_presupuesto`, igual que el clon de siempre).
    comps_base = [
        {"concepto": c.concepto, "galeno_id": c.galeno_id, "cantidad": c.cantidad,
         "valor_unitario": c.valor_unitario, "orden": c.orden, "observacion": c.observacion}
        for c in (await db.execute(
            select(ValorComponente).where(
                ValorComponente.valor_id == base.id, ValorComponente.activo == True,
            )
        )).scalars()
    ]
    await service.persistir_valores_en_bloque(db, [
        (
            {
                "obra_social_nro": os_nro, "nomenclador_id": base.nomenclador_id,
                "origen": "NE", "codigo": base.codigo, "descripcion": base.descripcion,
                "nivel": base.nivel, "complejidad": base.complejidad,
                "categoria": base.categoria,
                "requiere_autorizacion": base.requiere_autorizacion,
                "especialidad_id_colegio": esp,
                "sin_restriccion_especialidad": base.sin_restriccion_especialidad,
                "cantidad_ayudantes": base.cantidad_ayudantes, "coseguro": base.coseguro,
                "vigencia_desde": base.vigencia_desde, "observacion": base.observacion,
            },
            comps_base,
        )
        for esp in especialidades
    ], motivo="replicacion")
