"""Motor de cálculo por nomenclador: UNA fórmula para los componentes de un valor que se
factura "por nomenclador" (Cirugía adulto 7/10, infantil, FASGO, Urología…).

Cada nomenclador nivelado tiene su ESCALA (`nm_nomenclador_nivel`: unidades de honorarios
y de ayudante por nivel) y su galeno (`galeno_codigo`, y `galeno_gastos_codigo` para
gastos). El precio sale del galeno de la obra social —con su vigencia— multiplicado por
las unidades:

  - Honorarios: unidades del nivel del código × el galeno del nivel.
  - Ayudante: las unidades de ayudante del nivel (por defecto el 25 % de las de
    honorarios) × el mismo galeno. Al facturar, ese monto se divide según los ayudantes.
  - Gastos: `galeno_gastos_codigo` de la O.S. × las unidades de gastos del código.

Una obra social puede AJUSTAR las unidades de un nivel: si su galeno (`nm_galenos`)
tiene `unidades_honorarios` / `unidades_ayudante` distintas de NULL, mandan sobre la
escala. Si pactó PRECIO FIJO POR NIVEL (`precio_por_nivel`), cada nivel es un precio y el
galeno guarda 1 unidad de honorarios (+ 0,25 de ayudante): la misma fórmula lo resuelve.

La salida son los componentes de siempre (`galeno_id` + `cantidad`), por eso lookup de
precio, historial y facturación no cambian.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.nomenclador_cmc import Galeno, NomencladorCMC, NomencladorNivel, NomencladorNivelado

# El ayudante cobra el 25 % de los honorarios: en la escala y en el precio por nivel.
AYUDANTE_PCT = Decimal("0.25")
_DOS = Decimal("0.01")


def ayudante_por_defecto(unidades_honorarios: Decimal) -> Decimal:
    return (Decimal(unidades_honorarios) * AYUDANTE_PCT).quantize(_DOS)


async def escala_de(db: AsyncSession, nomenclador_nivelado_id: int) -> dict[int, tuple[Decimal, Optional[Decimal]]]:
    """`{nivel: (unidades_honorarios, unidades_ayudante o None = 25 %)}`."""
    filas = (await db.execute(
        select(NomencladorNivel).where(NomencladorNivel.nomenclador_nivelado_id == nomenclador_nivelado_id)
    )).scalars()
    return {f.nivel: (f.unidades_honorarios, f.unidades_ayudante) for f in filas}


def unidades_de_nivel(
    escala: dict[int, tuple[Decimal, Optional[Decimal]]], nivel: int, galeno: Optional[Galeno],
) -> tuple[Decimal, Decimal]:
    """`(unidades_honorarios, unidades_ayudante)` de un nivel para una O.S.: el ajuste de
    su galeno, si lo tiene; si no, la escala del nomenclador; el ayudante, el 25 %."""
    esc_h, esc_a = escala.get(nivel, (None, None))
    if galeno is not None and galeno.unidades_honorarios is not None:
        uh = Decimal(galeno.unidades_honorarios)
    elif esc_h is not None:
        uh = Decimal(esc_h)
    else:
        uh = Decimal("0")
    if galeno is not None and galeno.unidades_ayudante is not None:
        ua = Decimal(galeno.unidades_ayudante)
    elif esc_a is not None:
        ua = Decimal(esc_a)
    else:
        ua = ayudante_por_defecto(uh)
    return uh, ua


def componentes_nivelado(
    galeno: Galeno, gasto: Optional[Galeno], unidades_honorarios: Decimal, unidades_ayudante: Decimal,
    unidades_gastos: Decimal,
) -> list[dict]:
    """Los tres componentes calculables de un valor por nomenclador."""
    gas_galeno = gasto or galeno
    return [
        {"concepto": "Honorarios", "galeno_id": galeno.id, "cantidad": unidades_honorarios,
         "valor_unitario": None, "orden": 0, "observacion": None},
        {"concepto": "Gastos", "galeno_id": gas_galeno.id,
         "cantidad": unidades_gastos if gasto is not None else Decimal("0"),
         "valor_unitario": None, "orden": 1, "observacion": None},
        {"concepto": "Ayudante", "galeno_id": galeno.id, "cantidad": unidades_ayudante,
         "valor_unitario": None, "orden": 2, "observacion": None},
    ]


def componentes_de_codigo(
    nom: NomencladorCMC, nivel: Optional[int], unidades_fijas: Optional[Decimal],
    escala: dict[int, tuple[Decimal, Optional[Decimal]]], galeno_del_nivel: dict[int, Galeno],
    gasto: Optional[Galeno],
) -> tuple[list[dict], Galeno]:
    """Componentes de un código del nomenclador para una O.S. y el galeno de su nivel.

    Un código "de N unidades fijas" (FASGO) usa el nivel 1 con N unidades de honorarios y
    sin ayudante. Los demás, las unidades de su nivel."""
    from app.modules.nomenclador.routes_valores import _resolver_cantidad

    nivel_efectivo = nivel if nivel is not None else 1
    galeno = galeno_del_nivel[nivel_efectivo]
    if unidades_fijas is not None:
        uh, ua = Decimal(unidades_fijas), Decimal("0")
    else:
        uh, ua = unidades_de_nivel(escala, nivel_efectivo, galeno)
    ug = _resolver_cantidad(nom, gasto, "Gastos", Decimal("0")) if gasto is not None else Decimal("0")
    return componentes_nivelado(galeno, gasto, uh, ua, ug), galeno


async def nomenclador_por_galeno(db: AsyncSession, galeno_codigo: str) -> list[NomencladorNivelado]:
    """Los nomencladores nivelados que cotizan con ese galeno (p. ej. adulto 7 y 10)."""
    return list((await db.execute(
        select(NomencladorNivelado).where(
            NomencladorNivelado.galeno_codigo == galeno_codigo, NomencladorNivelado.tipo == "nivelado",
            NomencladorNivelado.activo == True,  # noqa: E712
        ).order_by(NomencladorNivelado.niveles)
    )).scalars())
