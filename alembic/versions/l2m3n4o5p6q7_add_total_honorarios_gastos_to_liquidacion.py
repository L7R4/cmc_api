"""add total_honorarios and total_gastos to liquidacion

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-04-13

"""
from alembic import op
import sqlalchemy as sa

revision = 'l2m3n4o5p6q7'
down_revision = 'k1l2m3n4o5p6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'liquidacion',
        sa.Column('total_honorarios', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
    )
    op.add_column(
        'liquidacion',
        sa.Column('total_gastos', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
    )


def downgrade() -> None:
    op.drop_column('liquidacion', 'total_gastos')
    op.drop_column('liquidacion', 'total_honorarios')
