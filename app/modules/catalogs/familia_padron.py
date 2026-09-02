"""Resolución de "familia" de obras sociales para padrón/socios.

Una empresa como SWISS MEDICAL opera con varios `NRO_OBRASOCIAL` que son
"planes" de la misma empresa. Para facturación/liquidación/valores siguen
100% diferenciados (cada uno con sus aranceles). Para el padrón de
prestadores son UNO solo: lo que se le manda a la empresa tiene que
englobar todos sus planes.

`ObrasSociales.obra_social_principal_id` (self-FK a `ObrasSociales.ID`) ya
modela esto, pero hasta ahora era puramente decorativo (sólo se usaba para
mostrar "principal"/"asociadas" en el CRUD de obras sociales). Este módulo
lo convierte en la fuente de verdad del agrupamiento para todo lo relativo
a padrones — nunca lo debe importar facturación/liquidación/valores, que
necesitan el `NRO_OBRASOCIAL` exacto de cada plan.
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.catalogs import ObrasSociales

log = logging.getLogger(__name__)


async def codigos_de_familia(db: AsyncSession, nro_os: int) -> list[int]:
    """`NRO_OBRASOCIAL` de toda la familia del código dado.

    Sube a la principal si `nro_os` es una asociada, y baja a todas las
    hermanas (incluida la principal misma). Si el código no tiene fila en
    `obras_sociales`, o no participa de ninguna relación, devuelve `[nro_os]`
    — mismo comportamiento que si no existiera este módulo.

    Resuelve en un único salto hacia arriba y uno hacia abajo — sin
    recursión. Eso es la guarda contra ciclos: es imposible entrar en loop
    sin importar qué grafo tenga la tabla. Si la fila que queda como raíz
    tiene a su vez `obra_social_principal_id` (cadena de profundidad > 1,
    hoy no vista en producción pero no impedida por el CRUD), se corta ahí
    y se loguea un WARNING para que un admin lo corrija.
    """
    row = (
        await db.execute(
            select(ObrasSociales).where(ObrasSociales.NRO_OBRASOCIAL == nro_os)
        )
    ).scalar_one_or_none()

    if row is None:
        return [nro_os]

    principal = row
    if row.obra_social_principal_id is not None:
        encontrada = (
            await db.execute(
                select(ObrasSociales).where(ObrasSociales.ID == row.obra_social_principal_id)
            )
        ).scalar_one_or_none()
        if encontrada is not None:
            principal = encontrada

    if principal.obra_social_principal_id is not None:
        log.warning(
            "familia_padron: cadena de profundidad > 1 detectada "
            "(NRO_OBRASOCIAL=%s -> principal NRO_OBRASOCIAL=%s, que a su vez "
            "tiene obra_social_principal_id=%s). Se corta en un nivel; "
            "revisar la relación en el CRUD de obras sociales.",
            nro_os, principal.NRO_OBRASOCIAL, principal.obra_social_principal_id,
        )

    asociadas = (
        await db.execute(
            select(ObrasSociales.NRO_OBRASOCIAL)
            .where(ObrasSociales.obra_social_principal_id == principal.ID)
        )
    ).scalars().all()

    return list({principal.NRO_OBRASOCIAL, *asociadas})


async def mapa_codigo_a_raiz(db: AsyncSession) -> dict[int, int]:
    """Todos los `NRO_OBRASOCIAL` del catálogo → `NRO_OBRASOCIAL` de su
    familia (la principal, o el propio código si no tiene familia).

    Para uso batch (BFF móvil, listados que resuelven muchos códigos a la
    vez) — evita repetir `codigos_de_familia()` por cada fila.
    """
    filas = (
        await db.execute(
            select(ObrasSociales.ID, ObrasSociales.NRO_OBRASOCIAL, ObrasSociales.obra_social_principal_id)
        )
    ).all()

    por_id = {id_: (nro, principal_id) for id_, nro, principal_id in filas}
    mapa: dict[int, int] = {}
    for id_, nro, principal_id in filas:
        if principal_id is None:
            mapa[nro] = nro
            continue
        principal = por_id.get(principal_id)
        if principal is None:
            mapa[nro] = nro
            continue
        principal_nro, principal_de_principal = principal
        if principal_de_principal is not None:
            log.warning(
                "familia_padron: cadena de profundidad > 1 detectada "
                "(NRO_OBRASOCIAL=%s -> principal NRO_OBRASOCIAL=%s, que a su vez "
                "tiene obra_social_principal_id=%s). Se corta en un nivel.",
                nro, principal_nro, principal_de_principal,
            )
        mapa[nro] = principal_nro

    return mapa
