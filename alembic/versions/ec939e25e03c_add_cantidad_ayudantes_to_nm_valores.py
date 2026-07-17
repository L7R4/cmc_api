"""add_cantidad_ayudantes_to_nm_valores

Revision ID: ec939e25e03c
Revises: 5e62e35f9810
Create Date: 2026-07-13

Máximo de ayudantes admitidos por (código, obra social). NULL = no lleva ayudantes.
Nullable, sin backfill: los valores existentes quedan en NULL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "ec939e25e03c"
down_revision: Union[str, None] = "5e62e35f9810"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "nm_valores",
        sa.Column("cantidad_ayudantes", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("nm_valores", "cantidad_ayudantes")
