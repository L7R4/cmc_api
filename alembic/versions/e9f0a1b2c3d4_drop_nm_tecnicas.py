"""drop_nm_tecnicas

Revision ID: e9f0a1b2c3d4
Revises: c3d4e5f6a7b8
Create Date: 2026-06-04

"""
from alembic import op
import sqlalchemy as sa

revision = 'e9f0a1b2c3d4'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table('nm_tecnicas')


def downgrade() -> None:
    op.create_table(
        'nm_tecnicas',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('codigo', sa.String(50), nullable=False),
        sa.Column('nombre', sa.String(100), nullable=False),
        sa.Column('descripcion', sa.Text(), nullable=True),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default='1'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('codigo', name='uq_nm_tecnicas_codigo'),
    )
