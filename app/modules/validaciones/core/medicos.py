"""Lecturas del médico compartidas por todas las obras sociales."""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ListadoMedico


async def get_medico(db: AsyncSession, nro_socio: int) -> ListadoMedico:
    medico = (
        await db.execute(select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == nro_socio))
    ).scalar_one_or_none()
    if medico is None:
        raise HTTPException(404, f"No existe el socio {nro_socio} en el registro del Colegio.")
    return medico


def especialidades_de(medico: ListadoMedico) -> list[int]:
    """Las 6 columnas NRO_ESPECIALIDAD*, sin ceros ni repetidos."""
    crudas = [
        medico.NRO_ESPECIALIDAD,
        medico.NRO_ESPECIALIDAD2,
        medico.NRO_ESPECIALIDAD3,
        medico.NRO_ESPECIALIDAD4,
        medico.NRO_ESPECIALIDAD5,
        medico.NRO_ESPECIALIDAD6,
    ]
    vistas: list[int] = []
    for e in crudas:
        if e and int(e) not in vistas:
            vistas.append(int(e))
    return vistas
