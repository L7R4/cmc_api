"""Agregar columna color a beneficios

Acento de color (hex #RRGGBB) para la tarjeta del beneficio en la app móvil.
Aditivo y nullable: los beneficios existentes quedan sin color (default del app).

Revision ID: v5w6x7y8z9a0
Revises: u4v5w6x7y8z9
Create Date: 2026-07-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "v5w6x7y8z9a0"
down_revision: Union[str, None] = "u4v5w6x7y8z9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("beneficios", sa.Column("color", sa.String(7), nullable=True))


def downgrade() -> None:
    op.drop_column("beneficios", "color")
