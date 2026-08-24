"""Índice (vigencia_desde, obra_social_nro) en nm_valores

Sostiene `GET /api/valores_nm/actualizaciones`, que agrupa las 55.000 filas de
`nm_valores` por esas dos columnas para armar el resumen de actualizaciones por
mes.

Sin él la consulta hace un full scan con tabla temporal y filesort: 107 ms. Con
el índice es un covering index scan: 6,6 ms. Medido sobre la base de desarrollo.

Sólo agrega un índice. No toca datos, columnas ni permisos.

Revision ID: vigos1dx0001
Revises: ospag0s0001
Create Date: 2026-08-23
"""
from typing import Sequence, Union

from alembic import op

revision: str = "vigos1dx0001"
down_revision: Union[str, Sequence[str], None] = "ospag0s0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # El orden importa: `vigencia_desde` primero porque es la columna por la que
    # se agrupa y se ordena. Al revés, el índice no evitaría el filesort.
    op.create_index(
        "ix_nm_valores_vigencia_os", "nm_valores", ["vigencia_desde", "obra_social_nro"]
    )


def downgrade() -> None:
    op.drop_index("ix_nm_valores_vigencia_os", table_name="nm_valores")
