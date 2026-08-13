"""Registro de obras sociales integradas al panel de validaciones.

Agregar una obra social nueva es: crear la carpeta `obras/<nombre>/` con su
`ValidadorOS`, instanciarlo acá abajo y sumarlo a `VALIDADORES`. Nada más —
`routes.py` y `core/pipeline.py` no cambian.

**En migración (2026-08):** todavía no están los 6 acá. Las que faltan siguen
resolviéndose por el camino viejo en `service.py`/`routes.py`; se van sumando
una por una, moviendo su lógica a `obras/<os>/` y borrando su rama del if/elif
en el mismo commit — ver el plan de refactor.
"""
from fastapi import HTTPException

from app.modules.validaciones.core.contrato import ValidadorOS
from app.modules.validaciones.obras.boreal import BOREAL
from app.modules.validaciones.obras.nobis import NOBIS
from app.modules.validaciones.obras.omint import OMINT
from app.modules.validaciones.obras.ospjn import OSPJN
from app.modules.validaciones.obras.ospm import OSPM
from app.modules.validaciones.obras.sancor import SANCOR

VALIDADORES: tuple[ValidadorOS, ...] = (BOREAL, OMINT, OSPM, OSPJN, NOBIS, SANCOR)

POR_NRO: dict[int, ValidadorOS] = {v.nro: v for v in VALIDADORES}

assert len(POR_NRO) == len(VALIDADORES), "Hay obras sociales con `nro` repetido en VALIDADORES."


def obtener_o_error(nro: int) -> ValidadorOS:
    obra = POR_NRO.get(nro)
    if obra is None:
        raise HTTPException(422, _mensaje_no_implementada(nro))
    return obra


def _mensaje_no_implementada(nro: int) -> str:
    implementadas = [f"{v.nombre} ({v.nro}, {v.modalidad})" for v in VALIDADORES]
    return (
        f"La obra social {nro} todavía no está implementada en el panel. "
        "Sólo están: " + ", ".join(implementadas)
    )
