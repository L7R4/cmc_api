"""galenos_codigo_autoslug

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-06-13

Galenos: la clave-identidad `codigo` se deriva automáticamente del `nombre`
(slug en formato xxxx_xxxx_xxxx, minúsculas, sin acentos). La columna `tipo`
(enum galeno|gasto|modulo|otro) dejaba de aportar valor — solo era un filtro
opcional del GET, no participaba de ninguna constraint, lookup ni cálculo — y
se elimina junto con su índice. Los `codigo` legacy se conservan intactos
(son la identidad ya cableada a vigencias, valores e historial).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d9e0f1a2b3c4"
down_revision: Union[str, Sequence[str], None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_nm_galenos_tipo", table_name="nm_galenos")
    op.drop_column("nm_galenos", "tipo")


def downgrade() -> None:
    op.add_column(
        "nm_galenos",
        sa.Column(
            "tipo",
            sa.Enum("galeno", "gasto", "modulo", "otro", name="nm_tipo_galeno_enum"),
            nullable=False,
            server_default="galeno",
        ),
    )
    op.create_index("ix_nm_galenos_tipo", "nm_galenos", ["tipo"])
    # NOTA: el valor original de `tipo` por fila no se puede restaurar.
