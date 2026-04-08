"""Add 'sin_factura' to lote_tipo enum

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-04-04

Agrega el valor 'sin_factura' al enum lote_tipo en lote_ajuste.tipo.
Los lotes sin_factura no requieren un período cerrado en Periodos y
permiten ajustes con medico_id explícito (sin id_atencion).

MySQL 5.7 no soporta ALTER TYPE — se usa MODIFY COLUMN directo.
"""

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE lote_ajuste "
        "MODIFY COLUMN tipo ENUM('normal', 'refacturacion', 'sin_factura') NOT NULL DEFAULT 'normal'"
    )


def downgrade() -> None:
    # Antes de revertir, convertir cualquier sin_factura → normal para no perder datos
    op.execute("UPDATE lote_ajuste SET tipo = 'normal' WHERE tipo = 'sin_factura'")
    op.execute(
        "ALTER TABLE lote_ajuste "
        "MODIFY COLUMN tipo ENUM('normal', 'refacturacion') NOT NULL DEFAULT 'normal'"
    )
