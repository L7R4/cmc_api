"""deduccion_programa_y_pagador

Revision ID: cc46b413fdaf
Revises: c1a2b3d4e5f6
Create Date: 2026-03-23

Cambios:
- ALTER TABLE socio_descuento: agrega columna pagador_medico_id
- CREATE TABLE deduccion_programa: plan de deducciones manuales con cuotas
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'cc46b413fdaf'
down_revision: Union[str, Sequence[str], None] = 'c1a2b3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Agregar pagador_medico_id a socio_descuento
    op.add_column(
        'socio_descuento',
        sa.Column('pagador_medico_id', sa.Integer(), nullable=True),
    )
    op.create_index(
        'ix_socio_descuento_pagador_medico_id',
        'socio_descuento',
        ['pagador_medico_id'],
    )
    op.create_foreign_key(
        'fk_socio_descuento_pagador',
        'socio_descuento',
        'listado_medico',
        ['pagador_medico_id'],
        ['ID'],
    )

    # 2. Crear tabla deduccion_programa
    op.create_table(
        'deduccion_programa',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('medico_id', sa.Integer(), nullable=False),
        sa.Column('descuento_id', sa.Integer(), nullable=False),
        sa.Column('monto_total', sa.DECIMAL(14, 2), nullable=False),
        sa.Column('monto_cuota', sa.DECIMAL(14, 2), nullable=False),
        sa.Column('cuotas_total', sa.Integer(), server_default='1', nullable=False),
        sa.Column('cuota_nro', sa.Integer(), server_default='1', nullable=False),
        sa.Column('cuotificado', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('grupo_id', sa.Integer(), nullable=True),
        sa.Column('mes_aplicar', sa.Integer(), nullable=False),
        sa.Column('anio_aplicar', sa.Integer(), nullable=False),
        sa.Column('pagador_medico_id', sa.Integer(), nullable=True),
        sa.Column(
            'estado',
            sa.Enum('pendiente', 'en_pago', 'aplicado', 'cancelado', name='ded_prog_estado'),
            server_default='pendiente',
            nullable=False,
        ),
        sa.Column('pago_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('created_by_user', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['medico_id'], ['listado_medico.ID'], name='fk_dp_medico'),
        sa.ForeignKeyConstraint(['descuento_id'], ['descuentos.id'], name='fk_dp_descuento'),
        sa.ForeignKeyConstraint(['pagador_medico_id'], ['listado_medico.ID'], name='fk_dp_pagador'),
        sa.ForeignKeyConstraint(['pago_id'], ['pago.id'], name='fk_dp_pago'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_dp_medico', 'deduccion_programa', ['medico_id'])
    op.create_index('idx_dp_periodo', 'deduccion_programa', ['mes_aplicar', 'anio_aplicar'])
    op.create_index('idx_dp_grupo', 'deduccion_programa', ['grupo_id'])
    op.create_index('idx_dp_estado', 'deduccion_programa', ['estado'])
    op.create_index('idx_dp_pago', 'deduccion_programa', ['pago_id'])


def downgrade() -> None:
    op.drop_index('idx_dp_pago', table_name='deduccion_programa')
    op.drop_index('idx_dp_estado', table_name='deduccion_programa')
    op.drop_index('idx_dp_grupo', table_name='deduccion_programa')
    op.drop_index('idx_dp_periodo', table_name='deduccion_programa')
    op.drop_index('idx_dp_medico', table_name='deduccion_programa')
    op.drop_table('deduccion_programa')

    op.drop_constraint('fk_socio_descuento_pagador', 'socio_descuento', type_='foreignkey')
    op.drop_index('ix_socio_descuento_pagador_medico_id', table_name='socio_descuento')
    op.drop_column('socio_descuento', 'pagador_medico_id')
