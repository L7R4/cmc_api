"""add_version_to_facturacion_and_detalle

Revision ID: b3d5f7a9c1e0
Revises: ec939e25e03c
Create Date: 2026-07-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b3d5f7a9c1e0"
down_revision: Union[str, None] = "ec939e25e03c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ADD COLUMN con default reescribe la tabla completa en MySQL 5.7, lo que revalida
    # TODAS las filas/columnas existentes. `facturacion` y `detalle_facturacion` son
    # tablas legacy con zero-dates ('0000-00-00') que rompen esa revalidación bajo
    # sql_mode estricto — se relaja la sesión solo para estos ALTER.
    conn = op.get_bind()
    original_mode = conn.execute(sa.text("SELECT @@SESSION.sql_mode")).scalar()
    conn.execute(sa.text("SET SESSION sql_mode = ''"))
    try:
        op.add_column(
            "facturacion",
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        )
        op.add_column(
            "detalle_facturacion",
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        )
    finally:
        conn.execute(sa.text("SET SESSION sql_mode = :mode"), {"mode": original_mode})


def downgrade() -> None:
    op.drop_column("detalle_facturacion", "version")
    op.drop_column("facturacion", "version")
