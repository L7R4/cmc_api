"""add coseguro a nm_valores

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-09-08

Escrita a mano, no autogenerate (mismo motivo que las migraciones previas de
este archivo: evitar drift preexistente ajeno a este cambio). Agrega el importe
de coseguro pactado por (obra social, código, vigencia) — mismo concepto y
misma semántica que `detalle_facturacion.coseguro` (se descuenta del total a
liquidar). Va como metadato del valor, no como parte de la ecuación de precio:
default 0 no rompe ningún lookup existente.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, Sequence[str], None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'nm_valores',
        sa.Column('coseguro', sa.DECIMAL(14, 2), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('nm_valores', 'coseguro')
