"""merge heads

Revision ID: 0c92a4d351cc
Revises: 8055625763e3, 8476613a2f1d
Create Date: 2025-09-07 18:08:09.578475

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0c92a4d351cc'
down_revision: Union[str, Sequence[str], None] = ('8055625763e3', '8476613a2f1d')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
