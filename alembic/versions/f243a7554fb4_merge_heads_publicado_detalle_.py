"""merge heads: publicado_detalle_facturacion + noticias_obras_sociales

Revision ID: f243a7554fb4
Revises: 6ec559c1b78f, notos1nrm001
Create Date: 2026-09-29 08:48:31.863598

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f243a7554fb4'
down_revision: Union[str, Sequence[str], None] = ('6ec559c1b78f', 'notos1nrm001')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
