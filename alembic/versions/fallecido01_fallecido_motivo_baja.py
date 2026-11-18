"""Agregar fallecido y motivo_baja a listado_medico.

`fallecido` es un dato aparte del estado de la cuenta. `motivo_baja` se llena al pasar a `baja`; la
fecha de la baja ya está en `estado_cuenta_at`. Idempotente: si la columna existe no la vuelve a crear.

Revision ID: fallecido01
Revises: obstexto01
Create Date: 2026-11-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "fallecido01"
down_revision: Union[str, Sequence[str], None] = "obstexto01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLA = "listado_medico"


def _columnas() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(TABLA)}


def upgrade() -> None:
    existentes = _columnas()
    if "fallecido" not in existentes:
        op.add_column(
            TABLA, sa.Column("fallecido", sa.Boolean(), nullable=False, server_default="0")
        )
    if "motivo_baja" not in existentes:
        op.add_column(TABLA, sa.Column("motivo_baja", sa.String(500), nullable=True))


def downgrade() -> None:
    existentes = _columnas()
    if "motivo_baja" in existentes:
        op.drop_column(TABLA, "motivo_baja")
    if "fallecido" in existentes:
        op.drop_column(TABLA, "fallecido")
