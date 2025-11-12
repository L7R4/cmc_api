"""data backfill medicos + user_role

Revision ID: 737e86363d13
Revises: a20b0a0e1941
Create Date: 2025-11-12 04:06:26.667249

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '737e86363d13'
down_revision: Union[str, Sequence[str], None] = 'a20b0a0e1941'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
