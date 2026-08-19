"""merge nm_valores_documentos con nomenclador_por_obra_social

Revision ID: 07026af30714
Revises: nmv4ld0cs001, a6b7c8d9e0f1
Create Date: 2026-08-18 10:39:10.209999

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '07026af30714'
down_revision: Union[str, Sequence[str], None] = ('nmv4ld0cs001', 'a6b7c8d9e0f1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
