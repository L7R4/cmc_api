"""Reemplazar monto por honorarios+gastos en ajuste

Revision ID: d4e5f6a7b8c9
Revises: a1b2c3d4e5f6
Create Date: 2026-04-05

Separa el campo monto de la tabla ajuste en dos columnas:
  - honorarios: parte de honorarios del ajuste
  - gastos: parte de gastos del ajuste

El campo monto existente se migra completo a honorarios (gastos=0),
ya que no hay información histórica para discriminar.
Luego monto es eliminado.
"""

from alembic import op
import sqlalchemy as sa
from decimal import Decimal

revision = "d4e5f6a7b8c9"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Agregar columna honorarios (nullable temporalmente para poder migrar)
    op.add_column(
        "ajuste",
        sa.Column("honorarios", sa.DECIMAL(14, 2), nullable=True),
    )
    # 2. Agregar columna gastos
    op.add_column(
        "ajuste",
        sa.Column("gastos", sa.DECIMAL(14, 2), nullable=True),
    )

    # 3. Migrar datos: todo el monto existente va a honorarios; gastos = 0
    op.execute("UPDATE ajuste SET honorarios = monto, gastos = 0.00")

    # 4. Hacer NOT NULL con default
    op.alter_column(
        "ajuste", "honorarios",
        nullable=False,
        server_default="0.00",
        existing_type=sa.DECIMAL(14, 2),
    )
    op.alter_column(
        "ajuste", "gastos",
        nullable=False,
        server_default="0.00",
        existing_type=sa.DECIMAL(14, 2),
    )

    # 5. Eliminar columna monto
    op.drop_column("ajuste", "monto")


def downgrade() -> None:
    # 1. Recuperar monto como honorarios + gastos
    op.add_column(
        "ajuste",
        sa.Column("monto", sa.DECIMAL(14, 2), nullable=True),
    )
    op.execute("UPDATE ajuste SET monto = honorarios + gastos")
    op.alter_column(
        "ajuste", "monto",
        nullable=False,
        existing_type=sa.DECIMAL(14, 2),
    )

    # 2. Eliminar las nuevas columnas
    op.drop_column("ajuste", "gastos")
    op.drop_column("ajuste", "honorarios")
