"""add_cod_med_ejecutor_to_detalle_facturacion

Revision ID: c9d0e1f2a3b4
Revises: b3d5f7a9c1e0
Create Date: 2026-07-17

Agrega `cod_med_ejecutor` a `detalle_facturacion`: NRO_SOCIO del médico que ejecutó
la prestación cuando `cod_med` (el payee) apunta a una clínica (es_organizacion=1).
NULL cuando el payee ya es el propio médico. Ver `docs/api/facturacion*`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b3d5f7a9c1e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `detalle_facturacion` es una tabla legacy con zero-dates ('0000-00-00') que
    # rompen la revalidación del ALTER bajo sql_mode estricto — se relaja la sesión
    # solo para este cambio (mismo patrón que la migración de `revisado`).
    conn = op.get_bind()
    original_mode = conn.execute(sa.text("SELECT @@SESSION.sql_mode")).scalar()
    conn.execute(sa.text("SET SESSION sql_mode = ''"))
    try:
        op.add_column(
            "detalle_facturacion",
            sa.Column("cod_med_ejecutor", sa.String(length=20), nullable=True),
        )
    finally:
        conn.execute(sa.text("SET SESSION sql_mode = :mode"), {"mode": original_mode})


def downgrade() -> None:
    op.drop_column("detalle_facturacion", "cod_med_ejecutor")
