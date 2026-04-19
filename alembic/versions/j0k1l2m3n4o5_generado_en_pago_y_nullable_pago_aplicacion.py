"""add generado_en_pago_id to deducciones, nullable pago_id en aplicacion, nuevo unique con estado

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-04-09 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'j0k1l2m3n4o5'
down_revision: Union[str, Sequence[str], None] = 'i9j0k1l2m3n4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Agregar generado_en_pago_id a deducciones ─────────────────────────
    op.add_column(
        'deducciones',
        sa.Column(
            'generado_en_pago_id',
            sa.Integer(),
            sa.ForeignKey('pago.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )
    op.create_index('ix_deducciones_generado_en_pago_id', 'deducciones', ['generado_en_pago_id'])

    # ── 2. Reemplazar unique constraint: quitar la vieja, agregar nueva con estado ──
    # MySQL nombra los constraints igual que el índice; intentamos ambos nombres comunes.
    try:
        op.drop_constraint('uq_ded_med_desc_per_cuota', 'deducciones', type_='unique')
    except Exception:
        try:
            op.drop_index('uq_ded_med_desc_per_cuota', table_name='deducciones')
        except Exception:
            pass

    op.create_unique_constraint(
        'uq_ded_med_desc_per_cuota_estado',
        'deducciones',
        ['medico_id', 'descuento_id', 'mes_aplicar', 'anio_aplicar', 'cuota_nro', 'estado'],
    )

    # ── 3. Hacer pago_id nullable en deduccion_aplicacion ────────────────────
    try:
        op.drop_constraint('deduccion_aplicacion_ibfk_1', 'deduccion_aplicacion', type_='foreignkey')
    except Exception:
        pass

    op.alter_column(
        'deduccion_aplicacion',
        'pago_id',
        existing_type=sa.Integer(),
        nullable=True,
    )

    op.create_foreign_key(
        'fk_dedapli_pago',
        'deduccion_aplicacion',
        'pago',
        ['pago_id'],
        ['id'],
        ondelete='RESTRICT',
    )


def downgrade() -> None:
    # Revertir pago_id a NOT NULL
    op.drop_constraint('fk_dedapli_pago', 'deduccion_aplicacion', type_='foreignkey')
    op.alter_column(
        'deduccion_aplicacion',
        'pago_id',
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_foreign_key(
        'deduccion_aplicacion_ibfk_1',
        'deduccion_aplicacion',
        'pago',
        ['pago_id'],
        ['id'],
        ondelete='RESTRICT',
    )

    # Revertir unique constraint
    try:
        op.drop_constraint('uq_ded_med_desc_per_cuota_estado', 'deducciones', type_='unique')
    except Exception:
        try:
            op.drop_index('uq_ded_med_desc_per_cuota_estado', table_name='deducciones')
        except Exception:
            pass

    op.create_unique_constraint(
        'uq_ded_med_desc_per_cuota',
        'deducciones',
        ['medico_id', 'descuento_id', 'mes_aplicar', 'anio_aplicar', 'cuota_nro'],
    )

    # Quitar generado_en_pago_id
    op.drop_index('ix_deducciones_generado_en_pago_id', table_name='deducciones')
    op.drop_column('deducciones', 'generado_en_pago_id')
