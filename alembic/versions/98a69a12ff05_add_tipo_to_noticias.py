"""add tipo to noticias

Revision ID: 98a69a12ff05
Revises: 737e86363d13
Create Date: 2025-12-21 20:02:37.405931

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '98a69a12ff05'
down_revision: Union[str, Sequence[str], None] = '737e86363d13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
