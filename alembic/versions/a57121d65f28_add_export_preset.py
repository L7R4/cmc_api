"""add export_preset

Revision ID: a57121d65f28
Revises: 2c5a03c93bc7
Create Date: 2026-09-07 11:24:11.281162

Escrita a mano — el `--autogenerate` traía ~1800 líneas de drift preexistente
entre el modelo y la base (de otros módulos, ajeno a este cambio). Esta
migración toca únicamente la tabla nueva `export_preset`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a57121d65f28'
down_revision: Union[str, Sequence[str], None] = '2c5a03c93bc7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'export_preset',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('usuario', sa.String(length=30), nullable=False),
        sa.Column('nombre', sa.String(length=120), nullable=False),
        sa.Column('tipo_documento', sa.String(length=20), nullable=False),
        sa.Column('opciones', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_export_preset_usuario'), 'export_preset', ['usuario'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_export_preset_usuario'), table_name='export_preset')
    op.drop_table('export_preset')
