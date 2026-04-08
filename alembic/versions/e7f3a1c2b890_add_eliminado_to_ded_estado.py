"""Add 'eliminado' to ded_estado enum

Revision ID: e7f3a1c2b890
Revises: b9e2c4d1f073
Create Date: 2026-03-25

Cambios:
- ALTER TABLE deducciones: agrega 'eliminado' al ENUM de estado
"""
from typing import Sequence, Union
from alembic import op

revision: str = "e7f3a1c2b890"
down_revision: Union[str, None] = "b9e2c4d1f073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE deducciones MODIFY COLUMN estado "
        "ENUM('pendiente','en_pago','aplicado','cancelado','eliminado') NOT NULL DEFAULT 'en_pago'"
    )


def downgrade() -> None:
    # Primero convertir cualquier 'eliminado' a 'cancelado' para no perder datos
    op.execute("UPDATE deducciones SET estado = 'cancelado' WHERE estado = 'eliminado'")
    op.execute(
        "ALTER TABLE deducciones MODIFY COLUMN estado "
        "ENUM('pendiente','en_pago','aplicado','cancelado') NOT NULL DEFAULT 'en_pago'"
    )
