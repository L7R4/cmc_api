"""drop_valor_componente_opcional

Elimina la columna `nm_valor_componentes.opcional`. Se descontinúa el concepto de
componente opcional: todo Valor lleva siempre los 3 conceptos (Honorarios/Gastos/
Ayudante) y todos suman al precio_total. El lookup ya no usa `opcional`/`incluido`
ni `opcionales_activos`.

Migración ESCRITA A MANO de forma aditiva (el autogenerate del repo arrastra diffs
destructivos por drift modelos↔DB legacy — ver 7619682b5ae9).

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-06-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, Sequence[str], None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column('nm_valor_componentes', 'opcional')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'nm_valor_componentes',
        sa.Column(
            'opcional', sa.Boolean(),
            nullable=False, server_default=sa.text('0'),
        ),
    )
