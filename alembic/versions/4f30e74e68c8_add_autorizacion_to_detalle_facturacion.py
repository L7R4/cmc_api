"""add_autorizacion_to_detalle_facturacion

Revision ID: 4f30e74e68c8
Revises: 6e77c0e80eba
Create Date: 2026-07-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "4f30e74e68c8"
down_revision: Union[str, None] = "6e77c0e80eba"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Mismo motivo que revisado (6e77c0e80eba): ADD COLUMN revalida la tabla completa
    # bajo MySQL 5.7 y las zero-dates legacy rompen esa revalidación en modo estricto.
    conn = op.get_bind()
    original_mode = conn.execute(sa.text("SELECT @@SESSION.sql_mode")).scalar()
    conn.execute(sa.text("SET SESSION sql_mode = ''"))
    try:
        op.add_column(
            "detalle_facturacion",
            sa.Column("autorizacion", sa.String(length=30), nullable=True),
        )
    finally:
        conn.execute(sa.text("SET SESSION sql_mode = :mode"), {"mode": original_mode})


def downgrade() -> None:
    op.drop_column("detalle_facturacion", "autorizacion")
