"""valores_por_presupuesto

Agrega la columna `por_presupuesto` (bool) a `nm_valores`. Marca un código (item de
valor por OS) como facturable "por presupuesto": no hay precio pactado en el sistema,
los componentes H/G/A quedan en 0 y la OS informa el monto por fuera (se carga a mano
al facturar).

Migración ESCRITA A MANO de forma aditiva (el autogenerate del repo arrastra diffs
destructivos por drift modelos↔DB legacy — ver 7619682b5ae9).

Revision ID: a1c2e3f40506
Revises: 7619682b5ae9
Create Date: 2026-06-15 17:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1c2e3f40506'
down_revision: Union[str, Sequence[str], None] = '7619682b5ae9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (aditivo)."""
    op.add_column(
        'nm_valores',
        sa.Column(
            'por_presupuesto', sa.Boolean(),
            nullable=False, server_default=sa.text('0'),
        ),
    )
    op.create_index(
        'ix_nm_valores_por_presupuesto', 'nm_valores', ['por_presupuesto'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_nm_valores_por_presupuesto', table_name='nm_valores')
    op.drop_column('nm_valores', 'por_presupuesto')
