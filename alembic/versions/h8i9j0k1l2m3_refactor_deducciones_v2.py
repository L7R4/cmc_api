"""refactor deducciones v2 — eliminar pago_id/deduccion_saldo, agregar dirty flag

Revision ID: h8i9j0k1l2m3
Revises: a3f91c2b8d40
Create Date: 2026-04-08 00:00:00.000000

Cambios:
- deducciones: TRUNCATE, DROP pago_id, nuevo UNIQUE (medico_id, descuento_id, mes_aplicar, anio_aplicar, cuota_nro)
- deduccion_aplicacion: TRUNCATE, DROP medico_id/concepto_tipo/concepto_id, ADD deduccion_id FK
- deduccion_saldo: DROP TABLE
- socio_descuento: ADD paga_por_caja BOOLEAN DEFAULT FALSE
- pago: ADD deducciones_dirty BOOLEAN DEFAULT FALSE
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = 'h8i9j0k1l2m3'
down_revision: Union[str, Sequence[str], None] = 'g2h3i4j5k6l7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ----------------------------------------------------------------
    # 1. TRUNCAR deducciones y deduccion_aplicacion (datos viejos)
    # ----------------------------------------------------------------
    conn.execute(sa.text("SET FOREIGN_KEY_CHECKS = 0"))
    conn.execute(sa.text("TRUNCATE TABLE deduccion_aplicacion"))
    conn.execute(sa.text("TRUNCATE TABLE deducciones"))
    conn.execute(sa.text("TRUNCATE TABLE deduccion_saldo"))
    conn.execute(sa.text("SET FOREIGN_KEY_CHECKS = 1"))

    # ----------------------------------------------------------------
    # 2. deducciones — eliminar pago_id y cambiar unique constraint
    # ----------------------------------------------------------------
    # Drop old FK constraint (nombre puede variar, usamos try/except via raw sql)
    try:
        op.drop_constraint('deducciones_ibfk_2', 'deducciones', type_='foreignkey')
    except Exception:
        pass
    # Intentar con nombre alternativo
    try:
        op.drop_constraint('deducciones_pago_id_fk', 'deducciones', type_='foreignkey')
    except Exception:
        pass
    # Drop via information_schema para encontrar el nombre real
    result = conn.execute(sa.text("""
        SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'deducciones'
          AND COLUMN_NAME = 'pago_id'
          AND REFERENCED_TABLE_NAME = 'pago'
        LIMIT 1
    """))
    row = result.fetchone()
    if row:
        conn.execute(sa.text(f"ALTER TABLE deducciones DROP FOREIGN KEY `{row[0]}`"))

    # Drop old unique constraint
    try:
        op.drop_constraint('uq_ded_pago_med_desc_cuota', 'deducciones', type_='unique')
    except Exception:
        pass

    # Drop index on pago_id
    try:
        op.drop_index('idx_ded_pago_med', table_name='deducciones')
    except Exception:
        pass

    # Drop the pago_id column
    op.drop_column('deducciones', 'pago_id')

    # Add new unique constraint
    op.create_unique_constraint(
        'uq_ded_med_desc_per_cuota',
        'deducciones',
        ['medico_id', 'descuento_id', 'mes_aplicar', 'anio_aplicar', 'cuota_nro'],
    )

    # ----------------------------------------------------------------
    # 3. deduccion_aplicacion — rebuild a (pago_id, deduccion_id, aplicado)
    # ----------------------------------------------------------------
    # Drop old constraints/indexes
    try:
        op.drop_constraint('uq_dedapli_pago_med_conc', 'deduccion_aplicacion', type_='unique')
    except Exception:
        pass
    try:
        op.drop_index('idx_apl_pago_med', table_name='deduccion_aplicacion')
    except Exception:
        pass
    try:
        op.drop_index('idx_apl_conc', table_name='deduccion_aplicacion')
    except Exception:
        pass

    # Drop old FK on medico_id if exists
    result2 = conn.execute(sa.text("""
        SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'deduccion_aplicacion'
          AND COLUMN_NAME = 'medico_id'
          AND REFERENCED_TABLE_NAME = 'listado_medico'
        LIMIT 1
    """))
    row2 = result2.fetchone()
    if row2:
        conn.execute(sa.text(f"ALTER TABLE deduccion_aplicacion DROP FOREIGN KEY `{row2[0]}`"))

    # Drop old columns
    op.drop_column('deduccion_aplicacion', 'medico_id')
    op.drop_column('deduccion_aplicacion', 'concepto_tipo')
    op.drop_column('deduccion_aplicacion', 'concepto_id')

    # Add deduccion_id
    op.add_column(
        'deduccion_aplicacion',
        sa.Column('deduccion_id', sa.Integer(), nullable=False),
    )
    op.create_foreign_key(
        'fk_dedapli_deduccion',
        'deduccion_aplicacion', 'deducciones',
        ['deduccion_id'], ['id'],
        ondelete='RESTRICT',
    )
    op.create_unique_constraint(
        'uq_dedapli_pago_ded',
        'deduccion_aplicacion',
        ['pago_id', 'deduccion_id'],
    )
    op.create_index('idx_apl_deduccion', 'deduccion_aplicacion', ['deduccion_id'])

    # ----------------------------------------------------------------
    # 4. DROP deduccion_saldo
    # ----------------------------------------------------------------
    op.drop_table('deduccion_saldo')

    # ----------------------------------------------------------------
    # 5. socio_descuento — agregar paga_por_caja
    # ----------------------------------------------------------------
    op.add_column(
        'socio_descuento',
        sa.Column(
            'paga_por_caja',
            sa.Boolean(),
            nullable=False,
            server_default='0',
        ),
    )

    # ----------------------------------------------------------------
    # 6. pago — agregar deducciones_dirty
    # ----------------------------------------------------------------
    op.add_column(
        'pago',
        sa.Column(
            'deducciones_dirty',
            sa.Boolean(),
            nullable=False,
            server_default='0',
        ),
    )


def downgrade() -> None:
    # Revert pago
    op.drop_column('pago', 'deducciones_dirty')

    # Revert socio_descuento
    op.drop_column('socio_descuento', 'paga_por_caja')

    # Recrear deduccion_saldo
    op.create_table(
        'deduccion_saldo',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('medico_id', sa.Integer(), nullable=False),
        sa.Column('concepto_tipo', mysql.ENUM('desc', 'esp', name='ded_saldo_tipo'), nullable=False),
        sa.Column('concepto_id', sa.Integer(), nullable=False),
        sa.Column('saldo', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('created_by_user', sa.Integer(), nullable=True),
        sa.UniqueConstraint('medico_id', 'concepto_tipo', 'concepto_id', name='uq_saldo_med_concepto'),
    )

    # Nota: downgrade completo de deduccion_aplicacion y deducciones omitido
    # (datos ya truncados — no hay forma de restaurar)
