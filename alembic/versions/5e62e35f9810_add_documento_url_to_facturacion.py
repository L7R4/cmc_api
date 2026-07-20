"""add_documento_url_to_facturacion

Revision ID: 5e62e35f9810
Revises: 4f30e74e68c8
Create Date: 2026-07-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "5e62e35f9810"
down_revision: Union[str, None] = "4f30e74e68c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Mismo motivo que otras columnas agregadas a estas tablas legacy MyISAM: ADD
    # COLUMN revalida la tabla completa y las zero-dates rompen esa revalidación bajo
    # sql_mode estricto — se relaja la sesión solo para este ALTER.
    conn = op.get_bind()
    original_mode = conn.execute(sa.text("SELECT @@SESSION.sql_mode")).scalar()
    conn.execute(sa.text("SET SESSION sql_mode = ''"))
    try:
        op.add_column(
            "facturacion",
            sa.Column("documento_url", sa.String(length=300), nullable=True),
        )
    finally:
        conn.execute(sa.text("SET SESSION sql_mode = :mode"), {"mode": original_mode})


def downgrade() -> None:
    op.drop_column("facturacion", "documento_url")
