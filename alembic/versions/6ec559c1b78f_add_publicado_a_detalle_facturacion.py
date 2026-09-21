"""add_publicado_a_detalle_facturacion

Revision ID: 6ec559c1b78f
Revises: b8c9d5e6f7a8
Create Date: 2026-09-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "6ec559c1b78f"
down_revision: Union[str, None] = "b8c9d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ADD COLUMN con default reescribe la tabla completa en MySQL 5.7, lo que
    # revalida TODAS las filas/columnas existentes. `detalle_facturacion` es una
    # tabla legacy con zero-dates ('0000-00-00') en otras columnas que rompen esa
    # revalidación bajo sql_mode estricto — se relaja la sesión solo para este ALTER
    # (mismo criterio que 6e77c0e80eba, para `revisado`).
    conn = op.get_bind()
    original_mode = conn.execute(sa.text("SELECT @@SESSION.sql_mode")).scalar()
    conn.execute(sa.text("SET SESSION sql_mode = ''"))
    try:
        op.add_column(
            "detalle_facturacion",
            sa.Column("publicado", sa.Boolean(), nullable=False, server_default="0"),
        )
        # El endpoint de publicar/despublicar filtra y actualiza por (cod_obr,
        # periodo); el listado de facturas agrupa por lo mismo para saber si el
        # período está publicado. Mismo criterio que ix_detalle_facturacion_validacion.
        op.create_index(
            "ix_detalle_facturacion_publicado",
            "detalle_facturacion",
            ["cod_obr", "periodo", "publicado"],
        )
    finally:
        conn.execute(sa.text("SET SESSION sql_mode = :mode"), {"mode": original_mode})


def downgrade() -> None:
    op.drop_index("ix_detalle_facturacion_publicado", table_name="detalle_facturacion")
    op.drop_column("detalle_facturacion", "publicado")
