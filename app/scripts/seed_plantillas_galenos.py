"""
Seed de nm_galenos_plantilla — plantillas prearmadas de galenos.

Esta tabla es solo lectura vía API (GET /api/galenos/plantillas): la carga la hace
a mano el programador editando PLANTILLAS abajo y corriendo este script. No hay
endpoint de escritura ni acceso de usuarios.

Cada entrada de PLANTILLAS es un grupo completo (ej. un galeno nivelado con sus N
niveles). `codigo` debe coincidir con slugify_codigo(nombre) — se valida antes de
insertar, porque es el mismo slug que tendrá el galeno real en nm_galenos al
instanciarse vía POST /api/galenos/crear_niveles.

`valor_unitario` se carga siempre en 0 (informativo; el precio real se pacta por OS
al instanciar la plantilla). Las unidades (unidades_honorarios/ayudante/gastos) son
opcionales: completalas si querés que el formulario del front las traiga precargadas.

Idempotente: por cada grupo, borra sus filas existentes y las reinserta.

Ejecutar (con el proyecto en /app dentro del contenedor):
  docker exec fastapi python app/scripts/seed_plantillas_galenos.py
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from sqlalchemy import delete, select

from app.db.database import AsyncSessionLocal, engine
from app.db.models import GalenoPlantilla
from app.modules.nomenclador.schemas import slugify_codigo


@dataclass
class NivelPlantilla:
    nivel: Optional[int]
    unidades_honorarios: Optional[Decimal] = None
    unidades_ayudante: Optional[Decimal] = None
    unidades_gastos: Optional[Decimal] = None


@dataclass
class Plantilla:
    grupo: str
    codigo: str
    nombre: str
    niveles: list[NivelPlantilla] = field(default_factory=list)


def _pct(honorarios: int, pct: Decimal) -> Decimal:
    """unidades_ayudante = honorarios × pct, redondeado a 2 decimales (columna DECIMAL(10,2))."""
    return (Decimal(honorarios) * pct).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ─────────────────────────────────────────────────────────────────────────────
# Data a cargar — editar acá. Ejemplo: cirugía adulto con 7 niveles.
# ─────────────────────────────────────────────────────────────────────────────
PLANTILLAS: list[Plantilla] = [
    Plantilla(
        grupo="cirugia_adulto_de_7_niveles",
        codigo="cirugia_adulto",
        nombre="Cirugía Adulto",
        niveles=[NivelPlantilla(nivel=n) for n in range(1, 8)],
    ),
    Plantilla(
        grupo="cirugia_adulto_de_10_niveles",
        codigo="cirugia_adulto",
        nombre="Cirugía Adulto",
        niveles=[
            NivelPlantilla(nivel=1, unidades_honorarios=Decimal("3")),
            NivelPlantilla(nivel=2, unidades_honorarios=Decimal("10"), unidades_ayudante=_pct(10, Decimal("0.25"))),
            NivelPlantilla(nivel=3, unidades_honorarios=Decimal("20"), unidades_ayudante=_pct(20, Decimal("0.25"))),
            NivelPlantilla(nivel=4, unidades_honorarios=Decimal("30"), unidades_ayudante=_pct(30, Decimal("0.125"))),
            NivelPlantilla(nivel=5, unidades_honorarios=Decimal("45"), unidades_ayudante=_pct(45, Decimal("0.125"))),
            NivelPlantilla(nivel=6, unidades_honorarios=Decimal("65"), unidades_ayudante=_pct(65, Decimal("0.125"))),
            NivelPlantilla(nivel=7, unidades_honorarios=Decimal("90"), unidades_ayudante=_pct(90, Decimal("0.125"))),
            NivelPlantilla(nivel=8, unidades_honorarios=Decimal("120"), unidades_ayudante=_pct(120, Decimal("0.125"))),
            NivelPlantilla(nivel=9, unidades_honorarios=Decimal("150"), unidades_ayudante=_pct(150, Decimal("0.125"))),
            NivelPlantilla(nivel=10, unidades_honorarios=Decimal("170"), unidades_ayudante=_pct(170, Decimal("0.125"))),
        ],
    ),
]


async def _seed_plantilla(db, p: Plantilla) -> None:
    esperado = slugify_codigo(p.nombre)
    if esperado != p.codigo:
        raise ValueError(
            f"Plantilla '{p.grupo}': codigo '{p.codigo}' no coincide con "
            f"slugify_codigo(nombre)='{esperado}'"
        )
    if not p.niveles:
        raise ValueError(f"Plantilla '{p.grupo}': debe tener al menos un nivel")

    await db.execute(delete(GalenoPlantilla).where(GalenoPlantilla.grupo == p.grupo))
    for nv in p.niveles:
        db.add(
            GalenoPlantilla(
                grupo=p.grupo,
                codigo=p.codigo,
                nombre=p.nombre,
                nivel=nv.nivel,
                valor_unitario=Decimal("0"),
                unidades_honorarios=nv.unidades_honorarios,
                unidades_ayudante=nv.unidades_ayudante,
                unidades_gastos=nv.unidades_gastos,
            )
        )


async def main() -> None:
    async with AsyncSessionLocal() as db:
        for p in PLANTILLAS:
            await _seed_plantilla(db, p)
        await db.commit()

    async with AsyncSessionLocal() as db:
        total = (await db.execute(select(GalenoPlantilla))).scalars().all()
        print(f"nm_galenos_plantilla: {len(total)} filas en {len(PLANTILLAS)} grupo(s)")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
