"""add es_organizacion, cuenta_bancaria, adherente, interior to listado_medico

Revision ID: o5p6q7r8s9t0
Revises: n4o5p6q7r8s9
Create Date: 2026-04-21

"""
from alembic import op
import sqlalchemy as sa

revision = 'o5p6q7r8s9t0'
down_revision = 'n4o5p6q7r8s9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'listado_medico',
        sa.Column('es_organizacion', sa.Boolean(), nullable=False, server_default='0'),
    )
    op.add_column(
        'listado_medico',
        sa.Column('cuenta_bancaria', sa.String(20), nullable=True),
    )
    op.add_column(
        'listado_medico',
        sa.Column('adherente', sa.Boolean(), nullable=False, server_default='0'),
    )
    op.add_column(
        'listado_medico',
        sa.Column('interior', sa.Boolean(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('listado_medico', 'interior')
    op.drop_column('listado_medico', 'adherente')
    op.drop_column('listado_medico', 'cuenta_bancaria')
    op.drop_column('listado_medico', 'es_organizacion')
