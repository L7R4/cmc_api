"""add detalle_json to pago_medico, pago_medico_id to recibo, expand recibo estado enum

Revision ID: n4o5p6q7r8s9
Revises: m3n4o5p6q7r8
Create Date: 2026-04-14

"""
from alembic import op
import sqlalchemy as sa

revision = 'n4o5p6q7r8s9'
down_revision = 'm3n4o5p6q7r8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pago_medico: campo JSON para snapshot del detalle
    op.add_column(
        'pago_medico',
        sa.Column('detalle_json', sa.JSON(), nullable=True),
    )

    # recibo: FK a pago_medico
    op.add_column(
        'recibo',
        sa.Column('pago_medico_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_recibo_pago_medico',
        'recibo', 'pago_medico',
        ['pago_medico_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_index('idx_recibo_pago_medico', 'recibo', ['pago_medico_id'])

    # recibo: ampliar enum de estado (MySQL requiere MODIFY con todos los valores)
    op.alter_column(
        'recibo', 'estado',
        existing_type=sa.Enum('emitido', 'anulado', 'pagado', name='recibo_estado'),
        type_=sa.Enum('en_revision', 'liquidado', 'emitido', 'anulado', 'pagado', name='recibo_estado'),
        existing_nullable=False,
        server_default='en_revision',
    )


def downgrade() -> None:
    op.alter_column(
        'recibo', 'estado',
        existing_type=sa.Enum('en_revision', 'liquidado', 'emitido', 'anulado', 'pagado', name='recibo_estado'),
        type_=sa.Enum('emitido', 'anulado', 'pagado', name='recibo_estado'),
        existing_nullable=False,
        server_default='emitido',
    )
    op.drop_index('idx_recibo_pago_medico', table_name='recibo')
    op.drop_constraint('fk_recibo_pago_medico', 'recibo', type_='foreignkey')
    op.drop_column('recibo', 'pago_medico_id')
    op.drop_column('pago_medico', 'detalle_json')
