"""Agregar honorarios y gastos a detalle_liquidacion, renombrar importe a importe_total

Revision ID: e8f1a2b3c4d5
Revises: d4e5f6a7b8c9
Create Date: 2026-04-05

Cambios en detalle_liquidacion:
  - Agrega columna honorarios: importe_colegio * (cantidad * cant_tratamiento)
  - Agrega columna gastos: gastos * (cantidad * cant_tratamiento)
  - Renombra columna importe -> importe_total (= honorarios + gastos)

Los datos existentes migran: importe_total = importe (valor existente),
honorarios = importe (todo va a honorarios), gastos = 0,
ya que no hay información histórica para discriminar.
"""

from alembic import op
import sqlalchemy as sa

revision = "e8f1a2b3c4d5"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Agregar honorarios (nullable temporalmente para migrar datos)
    op.add_column(
        "detalle_liquidacion",
        sa.Column("honorarios", sa.DECIMAL(14, 2), nullable=True),
    )
    # 2. Agregar gastos (nullable temporalmente)
    op.add_column(
        "detalle_liquidacion",
        sa.Column("gastos", sa.DECIMAL(14, 2), nullable=True),
    )
    # 3. Agregar importe_total (nullable temporalmente)
    op.add_column(
        "detalle_liquidacion",
        sa.Column("importe_total", sa.DECIMAL(14, 2), nullable=True),
    )

    # 4. Migrar datos: honorarios = importe, gastos = 0, importe_total = importe
    op.execute(
        "UPDATE detalle_liquidacion SET honorarios = importe, gastos = 0.00, importe_total = importe"
    )

    # 5. Hacer NOT NULL con server_default
    op.alter_column(
        "detalle_liquidacion", "honorarios",
        nullable=False,
        server_default="0.00",
        existing_type=sa.DECIMAL(14, 2),
    )
    op.alter_column(
        "detalle_liquidacion", "gastos",
        nullable=False,
        server_default="0.00",
        existing_type=sa.DECIMAL(14, 2),
    )
    op.alter_column(
        "detalle_liquidacion", "importe_total",
        nullable=False,
        server_default="0.00",
        existing_type=sa.DECIMAL(14, 2),
    )

    # 6. Eliminar columna importe original
    op.drop_column("detalle_liquidacion", "importe")


def downgrade() -> None:
    # 1. Recuperar importe desde importe_total
    op.add_column(
        "detalle_liquidacion",
        sa.Column("importe", sa.DECIMAL(14, 2), nullable=True),
    )
    op.execute("UPDATE detalle_liquidacion SET importe = importe_total")
    op.alter_column(
        "detalle_liquidacion", "importe",
        nullable=False,
        existing_type=sa.DECIMAL(14, 2),
    )

    # 2. Eliminar las nuevas columnas
    op.drop_column("detalle_liquidacion", "importe_total")
    op.drop_column("detalle_liquidacion", "gastos")
    op.drop_column("detalle_liquidacion", "honorarios")
