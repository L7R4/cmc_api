"""fase2 nomenclador: nm_nomenclador.descripcion pasa a nullable

La descripción se movió a Valor.descripcion (por obra social) en la fase 2 de la
reestructura del nomenclador — esta columna queda sin usar (la lee solo el
fallback legacy) y ya no la completa el alta nueva de códigos, así que no puede
seguir siendo NOT NULL. No se borra ni se toca ningún dato existente.

Revision ID: nn2fase2rel1
Revises: nn1valesp001
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "nn2fase2rel1"
down_revision: Union[str, Sequence[str], None] = "nn1valesp001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "nm_nomenclador", "descripcion",
        existing_type=sa.String(length=255), nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "nm_nomenclador", "descripcion",
        existing_type=sa.String(length=255), nullable=False,
    )
