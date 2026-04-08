"""Unificar deduccion_programa en deducciones (tabla única)

Revision ID: b9e2c4d1f073
Revises: cc46b413fdaf
Create Date: 2026-03-23

Cambios:
- ALTER TABLE deducciones:
    * pago_id → nullable
    * DROP COLUMN pagado
    * ADD COLUMN origen ENUM('manual','automatico')
    * ADD COLUMN estado ENUM('pendiente','en_pago','aplicado','cancelado')
    * ADD COLUMN monto_total, monto_cuota, cuotas_total, cuota_nro, cuotificado
    * ADD COLUMN grupo_id, mes_aplicar, anio_aplicar, pagador_medico_id
    * DROP CONSTRAINT uq_ded_pago_med_desc
    * ADD CONSTRAINT uq_ded_pago_med_desc_cuota (pago_id, medico_id, descuento_id, cuota_nro)
- DROP TABLE deduccion_programa (con sus índices y FKs)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b9e2c4d1f073'
down_revision: Union[str, Sequence[str], None] = 'cc46b413fdaf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _has_unique(inspector: sa.Inspector, table_name: str, constraint_name: str) -> bool:
    return any(
        constraint["name"] == constraint_name
        for constraint in inspector.get_unique_constraints(table_name)
    )


def _has_foreign_key(inspector: sa.Inspector, table_name: str, fk_name: str) -> bool:
    return any(fk["name"] == fk_name for fk in inspector.get_foreign_keys(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # -------------------------------------------------------------------------
    # 1. ALTER TABLE deducciones
    # -------------------------------------------------------------------------

    # 1a. Make pago_id nullable
    op.alter_column(
        'deducciones', 'pago_id',
        existing_type=sa.Integer(),
        nullable=True,
    )

    # 1b. Drop pagado column
    if _has_column(inspector, 'deducciones', 'pagado'):
        op.drop_column('deducciones', 'pagado')

    # 1c. Add origen ENUM
    if not _has_column(inspector, 'deducciones', 'origen'):
        op.add_column(
            'deducciones',
            sa.Column(
                'origen',
                sa.Enum('manual', 'automatico', name='ded_origen'),
                nullable=False,
                server_default='automatico',
            ),
        )

    # 1d. Add estado ENUM (replaces pagado; default 'en_pago' for existing auto rows)
    if not _has_column(inspector, 'deducciones', 'estado'):
        op.add_column(
            'deducciones',
            sa.Column(
                'estado',
                sa.Enum('pendiente', 'en_pago', 'aplicado', 'cancelado', name='ded_estado'),
                nullable=False,
                server_default='en_pago',
            ),
        )

    # 1e. Add cuota_nro (0 = auto row, 1..N = manual cuota number)
    if not _has_column(inspector, 'deducciones', 'cuota_nro'):
        op.add_column(
            'deducciones',
            sa.Column('cuota_nro', sa.Integer(), nullable=False, server_default='0'),
        )

    # 1f. Add manual/cuota fields (nullable — only set for manual rows)
    if not _has_column(inspector, 'deducciones', 'monto_total'):
        op.add_column('deducciones', sa.Column('monto_total', sa.DECIMAL(14, 2), nullable=True))
    if not _has_column(inspector, 'deducciones', 'monto_cuota'):
        op.add_column('deducciones', sa.Column('monto_cuota', sa.DECIMAL(14, 2), nullable=True))
    if not _has_column(inspector, 'deducciones', 'cuotas_total'):
        op.add_column('deducciones', sa.Column('cuotas_total', sa.Integer(), nullable=True))
    if not _has_column(inspector, 'deducciones', 'cuotificado'):
        op.add_column('deducciones', sa.Column('cuotificado', sa.Boolean(), nullable=True))
    if not _has_column(inspector, 'deducciones', 'grupo_id'):
        op.add_column('deducciones', sa.Column('grupo_id', sa.Integer(), nullable=True))
    if not _has_column(inspector, 'deducciones', 'mes_aplicar'):
        op.add_column('deducciones', sa.Column('mes_aplicar', sa.Integer(), nullable=True))
    if not _has_column(inspector, 'deducciones', 'anio_aplicar'):
        op.add_column('deducciones', sa.Column('anio_aplicar', sa.Integer(), nullable=True))
    if not _has_column(inspector, 'deducciones', 'pagador_medico_id'):
        op.add_column(
            'deducciones',
            sa.Column('pagador_medico_id', sa.Integer(), nullable=True),
        )

    # 1g. Drop old unique constraint and add new one that includes cuota_nro
    if _has_unique(inspector, 'deducciones', 'uq_ded_pago_med_desc'):
        op.drop_constraint('uq_ded_pago_med_desc', 'deducciones', type_='unique')
    if not _has_unique(inspector, 'deducciones', 'uq_ded_pago_med_desc_cuota'):
        op.create_unique_constraint(
            'uq_ded_pago_med_desc_cuota',
            'deducciones',
            ['pago_id', 'medico_id', 'descuento_id', 'cuota_nro'],
        )

    # 1h. Add indexes
    if not _has_index(inspector, 'deducciones', 'idx_ded_origen_estado'):
        op.create_index('idx_ded_origen_estado', 'deducciones', ['origen', 'estado'])
    if not _has_index(inspector, 'deducciones', 'idx_ded_periodo'):
        op.create_index('idx_ded_periodo', 'deducciones', ['mes_aplicar', 'anio_aplicar'])
    if not _has_index(inspector, 'deducciones', 'idx_ded_grupo'):
        op.create_index('idx_ded_grupo', 'deducciones', ['grupo_id'])

    # 1i. FK for pagador_medico_id
    if not _has_foreign_key(inspector, 'deducciones', 'fk_ded_pagador'):
        op.create_foreign_key(
            'fk_ded_pagador',
            'deducciones',
            'listado_medico',
            ['pagador_medico_id'],
            ['ID'],
        )

    # -------------------------------------------------------------------------
    # 2. DROP TABLE deduccion_programa
    # -------------------------------------------------------------------------
    inspector = sa.inspect(bind)
    if _has_table(inspector, 'deduccion_programa'):
        op.drop_table('deduccion_programa')


def downgrade() -> None:
    # Recreate deduccion_programa
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

    # Revert deducciones
    op.drop_index('idx_ded_grupo', table_name='deducciones')
    op.drop_index('idx_ded_periodo', table_name='deducciones')
    op.drop_index('idx_ded_origen_estado', table_name='deducciones')
    op.drop_constraint('fk_ded_pagador', 'deducciones', type_='foreignkey')
    op.drop_constraint('uq_ded_pago_med_desc_cuota', 'deducciones', type_='unique')

    op.drop_column('deducciones', 'pagador_medico_id')
    op.drop_column('deducciones', 'anio_aplicar')
    op.drop_column('deducciones', 'mes_aplicar')
    op.drop_column('deducciones', 'grupo_id')
    op.drop_column('deducciones', 'cuotificado')
    op.drop_column('deducciones', 'cuotas_total')
    op.drop_column('deducciones', 'monto_cuota')
    op.drop_column('deducciones', 'monto_total')
    op.drop_column('deducciones', 'cuota_nro')
    op.drop_column('deducciones', 'estado')
    op.drop_column('deducciones', 'origen')

    op.add_column(
        'deducciones',
        sa.Column('pagado', sa.Boolean(), nullable=False, server_default='0'),
    )

    op.create_unique_constraint(
        'uq_ded_pago_med_desc',
        'deducciones',
        ['pago_id', 'medico_id', 'descuento_id'],
    )

    op.alter_column(
        'deducciones', 'pago_id',
        existing_type=sa.Integer(),
        nullable=False,
    )
