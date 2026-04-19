"""drop grupo_id from deducciones

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-04-09 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'i9j0k1l2m3n4'
down_revision: Union[str, Sequence[str], None] = 'h8i9j0k1l2m3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop index before dropping the column
    try:
        op.drop_index('ix_deducciones_grupo_id', table_name='deducciones')
    except Exception:
        pass
    op.drop_column('deducciones', 'grupo_id')


def downgrade() -> None:
    op.add_column(
        'deducciones',
        sa.Column('grupo_id', sa.Integer(), nullable=True),
    )
    op.create_index('ix_deducciones_grupo_id', 'deducciones', ['grupo_id'])
