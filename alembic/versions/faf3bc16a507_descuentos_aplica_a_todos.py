"""descuentos_aplica_a_todos

Revision ID: faf3bc16a507
Revises: a3f91c2b8d40
Create Date: 2026-03-05 13:59:52.595549

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'faf3bc16a507'
down_revision: Union[str, Sequence[str], None] = 'a3f91c2b8d40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'descuentos',
        sa.Column('aplica_a_todos', sa.Boolean(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('descuentos', 'aplica_a_todos')
