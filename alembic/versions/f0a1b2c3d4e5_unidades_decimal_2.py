"""unidades_decimal_2

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-06-04

"""
from alembic import op
import sqlalchemy as sa

revision = 'f0a1b2c3d4e5'
down_revision = 'e9f0a1b2c3d4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('nm_nomenclador', 'unidades_honorarios',
                    existing_nullable=True,
                    type_=sa.DECIMAL(10, 2))
    op.alter_column('nm_nomenclador', 'unidades_ayudante',
                    existing_nullable=True,
                    type_=sa.DECIMAL(10, 2))
    op.alter_column('nm_nomenclador', 'unidades_gastos',
                    existing_nullable=True,
                    type_=sa.DECIMAL(10, 2))


def downgrade() -> None:
    op.alter_column('nm_nomenclador', 'unidades_honorarios',
                    existing_nullable=True,
                    type_=sa.DECIMAL(10, 4))
    op.alter_column('nm_nomenclador', 'unidades_ayudante',
                    existing_nullable=True,
                    type_=sa.DECIMAL(10, 4))
    op.alter_column('nm_nomenclador', 'unidades_gastos',
                    existing_nullable=True,
                    type_=sa.DECIMAL(10, 4))
