"""Add 'AP' (Aplicado) to lote_estado enum

Revision ID: f1a2b3c4d5e6
Revises: e7f3a1c2b890
Create Date: 2026-03-28

Agrega el estado 'AP' (Aplicado) al enum lote_estado en la tabla lote_ajuste.
- AP = lote ya aplicado en un pago cerrado, inmutable.
  Solo puede volver a 'C' si el pago es eliminado.

MySQL 5.7 no soporta ALTER TYPE — se usa MODIFY COLUMN directo.
"""

from alembic import op

revision = "f1a2b3c4d5e6"
down_revision = "e7f3a1c2b890"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE lote_ajuste "
        "MODIFY COLUMN estado ENUM('A','C','L','AP') NOT NULL DEFAULT 'A'"
    )


def downgrade() -> None:
    # Antes de revertir, convertir cualquier AP → C para no perder datos
    op.execute("UPDATE lote_ajuste SET estado = 'C' WHERE estado = 'AP'")
    op.execute(
        "ALTER TABLE lote_ajuste "
        "MODIFY COLUMN estado ENUM('A','C','L') NOT NULL DEFAULT 'A'"
    )
