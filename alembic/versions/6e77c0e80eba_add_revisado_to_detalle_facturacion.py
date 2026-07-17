"""add_revisado_to_detalle_facturacion

Revision ID: 6e77c0e80eba
Revises: d7e8f9a0b1c2
Create Date: 2026-07-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "6e77c0e80eba"
down_revision: Union[str, None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ADD COLUMN con default reescribe la tabla completa en MySQL 5.7, lo que
    # revalida TODAS las filas/columnas existentes. `detalle_facturacion` es una
    # tabla legacy con zero-dates ('0000-00-00') en otras columnas que rompen esa
    # revalidación bajo sql_mode estricto — se relaja la sesión solo para este ALTER.
    conn = op.get_bind()
    original_mode = conn.execute(sa.text("SELECT @@SESSION.sql_mode")).scalar()
    conn.execute(sa.text("SET SESSION sql_mode = ''"))
    try:
        op.add_column(
            "detalle_facturacion",
            sa.Column("revisado", sa.Boolean(), nullable=False, server_default="0"),
        )
    finally:
        conn.execute(sa.text("SET SESSION sql_mode = :mode"), {"mode": original_mode})


def downgrade() -> None:
    op.drop_column("detalle_facturacion", "revisado")
