"""nomenclador_unidades_por_defecto

Revision ID: b2c3d4e5f6a7
Revises: aa1bb2cc3dd4, r5s6t7u8v9w0
Create Date: 2026-06-04

"""
from alembic import op
import sqlalchemy as sa

revision = 'b2c3d4e5f6a7'
down_revision = ('aa1bb2cc3dd4', 'r5s6t7u8v9w0')
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('nm_nomenclador', sa.Column('unidades_honorarios', sa.DECIMAL(10, 4), nullable=True))
    op.add_column('nm_nomenclador', sa.Column('unidades_ayudante',   sa.DECIMAL(10, 4), nullable=True))
    op.add_column('nm_nomenclador', sa.Column('unidades_gastos',     sa.DECIMAL(10, 4), nullable=True))


def downgrade() -> None:
    op.drop_column('nm_nomenclador', 'unidades_gastos')
    op.drop_column('nm_nomenclador', 'unidades_ayudante')
    op.drop_column('nm_nomenclador', 'unidades_honorarios')
