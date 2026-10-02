"""nm_nomenclador: agrega sin_restriccion_especialidad default (opcional)

Mismo criterio que la revisión anterior (`nn4nomdesc1`, descripción default a
nivel catálogo): agrega `sin_restriccion_especialidad` a `nm_nomenclador` como
default opcional, nullable. NULL = el catálogo no opina. La fuente de verdad
sigue siendo `Valor.sin_restriccion_especialidad`, por (OS, código) — ver
`service.especialidades_habilitadas_de`.

A diferencia de `descripcion`, esta columna no existía antes en `nm_nomenclador`
(se agregó directo en `nm_valores` en la fase 1 de la reestructura), así que no
hay backfill: se agrega vacía.

Revision ID: nn5sinrest1
Revises: nn4nomdesc1
Create Date: 2026-09-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "nn5sinrest1"
down_revision: Union[str, Sequence[str], None] = "nn4nomdesc1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "nm_nomenclador",
        sa.Column("sin_restriccion_especialidad", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("nm_nomenclador", "sin_restriccion_especialidad")
