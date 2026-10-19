"""nm_nomenclador_nivelado: lista de especialidades a las que se aplica el nomenclador

`especialidades` es un JSON con los `ID_COLEGIO_ESPE` ([7, 30, ...]). NULL o [] = todas las
especialidades del código (el comportamiento de siempre). Con lista, aplicar el nomenclador a una
obra social solo carga precio en las especialidades que están en la lista Y habilitadas para el
código en esa obra social.

Mismo DDL que `scripts/nivelados_especialidades_2026-10-12.sql` (producción no trackea
alembic_version, ahí se aplica a mano).

Revision ID: nivesp01
Revises: osactivo01
Create Date: 2026-10-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "nivesp01"
down_revision: Union[str, Sequence[str], None] = "osactivo01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("nm_nomenclador_nivelado", sa.Column("especialidades", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("nm_nomenclador_nivelado", "especialidades")
