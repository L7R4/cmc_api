"""valor_complejidad

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-04

"""
from alembic import op
import sqlalchemy as sa

revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'nm_valores',
        sa.Column(
            'complejidad',
            sa.Enum('baja', 'media', 'alta', name='nm_valor_complejidad_enum'),
            nullable=True,
        ),
    )
    op.create_index('ix_nm_valores_complejidad', 'nm_valores', ['complejidad'])


def downgrade() -> None:
    op.drop_index('ix_nm_valores_complejidad', 'nm_valores')
    op.drop_column('nm_valores', 'complejidad')
