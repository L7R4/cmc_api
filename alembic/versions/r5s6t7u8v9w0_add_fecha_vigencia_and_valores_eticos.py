"""add_fecha_vigencia_and_valores_eticos

Revision ID: r5s6t7u8v9w0
Revises: o5p6q7r8s9t0
Create Date: 2026-05-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = 'r5s6t7u8v9w0'
down_revision: Union[str, Sequence[str], None] = 'o5p6q7r8s9t0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('valor_prestacion',
        sa.Column('FECHA_VIGENCIA', sa.Date(), nullable=True))

    op.create_table(
        'valores_eticos',
        sa.Column('id', mysql.INTEGER(11), primary_key=True, autoincrement=True),
        sa.Column('pdf_path', sa.String(500), nullable=True),
        sa.Column('observaciones', sa.String(1000), nullable=True),
        sa.Column('fecha_update', sa.TIMESTAMP(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('valores_eticos')
    op.drop_column('valor_prestacion', 'FECHA_VIGENCIA')
