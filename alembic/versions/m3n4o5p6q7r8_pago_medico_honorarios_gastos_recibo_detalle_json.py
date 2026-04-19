"""add honorarios/gastos to pago_medico and detalle_json to recibo

Revision ID: m3n4o5p6q7r8
Revises: l2m3n4o5p6q7
Create Date: 2026-04-14

"""
from alembic import op
import sqlalchemy as sa

revision = 'm3n4o5p6q7r8'
down_revision = 'l2m3n4o5p6q7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pago_medico: agregar honorarios y gastos antes de bruto
    op.add_column(
        'pago_medico',
        sa.Column('honorarios', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
    )
    op.add_column(
        'pago_medico',
        sa.Column('gastos', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
    )
    # recibo: agregar campo JSON para snapshot del detalle
    op.add_column(
        'recibo',
        sa.Column('detalle_json', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('recibo', 'detalle_json')
    op.drop_column('pago_medico', 'gastos')
    op.drop_column('pago_medico', 'honorarios')
