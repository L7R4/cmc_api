"""add paga_por_caja column to deducciones

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-04-09 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'k1l2m3n4o5p6'
down_revision: Union[str, Sequence[str], None] = 'j0k1l2m3n4o5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'deducciones',
        sa.Column(
            'paga_por_caja',
            sa.Boolean(),
            nullable=False,
            server_default='0',
        ),
    )
    op.create_index('ix_deducciones_paga_por_caja', 'deducciones', ['paga_por_caja'])


def downgrade() -> None:
    op.drop_index('ix_deducciones_paga_por_caja', table_name='deducciones')
    op.drop_column('deducciones', 'paga_por_caja')
