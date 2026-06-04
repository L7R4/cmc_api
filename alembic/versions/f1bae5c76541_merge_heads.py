"""merge_heads

Revision ID: f1bae5c76541
Revises: o5p6q7r8s9t0, q3r4s5t6u7v8
Create Date: 2026-05-04 02:07:52.493430

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1bae5c76541'
down_revision: Union[str, Sequence[str], None] = ('o5p6q7r8s9t0', 'q3r4s5t6u7v8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
