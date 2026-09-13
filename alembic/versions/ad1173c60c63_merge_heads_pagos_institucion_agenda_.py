"""merge heads: pagos/institucion/agenda con nm_valores index

Revision ID: ad1173c60c63
Revises: c0br4nz4s001, vigos1dx0001
Create Date: 2026-09-02 14:52:43.819991

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ad1173c60c63'
down_revision: Union[str, Sequence[str], None] = ('c0br4nz4s001', 'vigos1dx0001')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
