"""refactor_pagos_lotes_ajustes

Revision ID: c1a2b3d4e5f6
Revises: faf3bc16a507
Create Date: 2026-03-17 00:00:00.000000

Refactorización completa del sistema de liquidaciones:
- liquidacion_resumen -> pago
- liquidacion_medico -> pago_medico
- debito_credito -> ajuste (dentro de lote_ajuste)
- Eliminación de recibo_item
- deducciones y deduccion_aplicacion usan pago_id en lugar de resumen_id/anio/mes

ADVERTENCIA: Esta migración elimina todos los datos de las tablas afectadas.
No hay downgrade (NotImplementedError).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c1a2b3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'faf3bc16a507'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# AuditMixin columns (created_at + created_by_user only)
_AUDIT = [
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
    sa.Column('created_by_user', sa.Integer(), nullable=True),
]


def upgrade() -> None:
    # ============================================================
    # 1. Eliminar tablas viejas (en orden para respetar FKs)
    # ============================================================
    op.execute("SET FOREIGN_KEY_CHECKS = 0")

    op.execute("DROP TABLE IF EXISTS recibo_item")
    op.execute("DROP TABLE IF EXISTS recibo")
    op.execute("DROP TABLE IF EXISTS liquidacion_medico")
    op.execute("DROP TABLE IF EXISTS deduccion_aplicacion")
    op.execute("DROP TABLE IF EXISTS deducciones")
    op.execute("DROP TABLE IF EXISTS debito_credito")
    op.execute("DROP TABLE IF EXISTS detalle_liquidacion")
    op.execute("DROP TABLE IF EXISTS liquidacion")
    op.execute("DROP TABLE IF EXISTS liquidacion_resumen")

    op.execute("SET FOREIGN_KEY_CHECKS = 1")

    # ============================================================
    # 2. Crear tabla pago (antes liquidacion_resumen)
    # ============================================================
    op.create_table(
        'pago',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('anio', sa.Integer(), nullable=False),
        sa.Column('mes', sa.Integer(), nullable=False),
        sa.Column('descripcion', sa.String(255), nullable=True),
        sa.Column('estado', sa.Enum('A', 'C', name='pago_estado'), nullable=False, server_default='A'),
        sa.Column('cierre_timestamp', sa.DateTime(), nullable=True),
        *_AUDIT,
        sa.PrimaryKeyConstraint('id'),
    )

    # ============================================================
    # 3. Crear tabla liquidacion (actualizada: pago_id, sin estado, sin refacturado_from)
    # ============================================================
    op.create_table(
        'liquidacion',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('pago_id', sa.Integer(), nullable=False),
        sa.Column('obra_social_id', sa.Integer(), nullable=False),
        sa.Column('mes_periodo', sa.Integer(), nullable=False),
        sa.Column('anio_periodo', sa.Integer(), nullable=False),
        sa.Column('nro_factura', sa.String(30), nullable=True),
        sa.Column('total_bruto', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
        sa.Column('total_debitos', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
        sa.Column('total_creditos', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
        sa.Column('total_neto', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
        *_AUDIT,
        sa.ForeignKeyConstraint(['pago_id'], ['pago.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('pago_id', 'obra_social_id', 'mes_periodo', 'anio_periodo', name='uq_liq_pago_os_per'),
    )
    op.create_index('idx_liq_pago_os_per', 'liquidacion', ['pago_id', 'obra_social_id', 'mes_periodo', 'anio_periodo'])
    op.create_index('idx_liq_obra_social', 'liquidacion', ['obra_social_id'])

    # ============================================================
    # 4. Crear tabla detalle_liquidacion (actualizada)
    # ============================================================
    op.create_table(
        'detalle_liquidacion',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('liquidacion_id', sa.Integer(), nullable=False),
        sa.Column('medico_id', sa.Integer(), nullable=False),
        sa.Column('obra_social_id', sa.Integer(), nullable=False),
        sa.Column('prestacion_id', sa.Integer(), nullable=False),
        sa.Column('pagado', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
        sa.Column('importe', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
        *_AUDIT,
        sa.ForeignKeyConstraint(['liquidacion_id'], ['liquidacion.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['prestacion_id'], ['guardar_atencion.ID'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('prestacion_id', 'liquidacion_id', 'medico_id', name='uq_det_prest_en_liq'),
    )
    op.create_index('idx_det_liq', 'detalle_liquidacion', ['liquidacion_id'])
    op.create_index('idx_det_medico', 'detalle_liquidacion', ['medico_id'])
    op.create_index('idx_det_prest', 'detalle_liquidacion', ['prestacion_id'])

    # ============================================================
    # 5. Crear tabla pago_medico (antes liquidacion_medico)
    # ============================================================
    op.create_table(
        'pago_medico',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('pago_id', sa.Integer(), nullable=False),
        sa.Column('medico_id', sa.Integer(), nullable=False),
        sa.Column('bruto', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
        sa.Column('debitos', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
        sa.Column('creditos', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
        sa.Column('reconocido', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
        sa.Column('deducciones', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
        sa.Column('neto_a_pagar', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
        sa.Column('estado', sa.Enum('pendiente', 'liquidado', 'pagado', name='pagomed_estado'), nullable=False, server_default='pendiente'),
        *_AUDIT,
        sa.ForeignKeyConstraint(['pago_id'], ['pago.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['medico_id'], ['listado_medico.ID'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('pago_id', 'medico_id', name='uq_pagomed_pago_med'),
    )
    op.create_index('idx_pagomed_pago', 'pago_medico', ['pago_id'])
    op.create_index('idx_pagomed_medico', 'pago_medico', ['medico_id'])

    # ============================================================
    # 6. Crear tabla recibo (actualizada: pago_id, sin items)
    # ============================================================
    op.create_table(
        'recibo',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('nro_recibo', sa.String(30), nullable=False),
        sa.Column('pago_id', sa.Integer(), nullable=False),
        sa.Column('medico_id', sa.Integer(), nullable=False),
        sa.Column('total_neto', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
        sa.Column('emision_timestamp', sa.DateTime(), nullable=True),
        sa.Column('estado', sa.Enum('emitido', 'anulado', 'pagado', name='recibo_estado'), nullable=False, server_default='emitido'),
        *_AUDIT,
        sa.ForeignKeyConstraint(['pago_id'], ['pago.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['medico_id'], ['listado_medico.ID'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('pago_id', 'medico_id', name='uq_recibo_pago_med'),
    )
    op.create_index('idx_recibo_pago', 'recibo', ['pago_id'])
    op.create_index('idx_recibo_med', 'recibo', ['medico_id'])
    op.create_index('idx_recibo_nro', 'recibo', ['nro_recibo'])

    # ============================================================
    # 7. Crear tabla lote_ajuste (nueva)
    # ============================================================
    op.create_table(
        'lote_ajuste',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('obra_social_id', sa.Integer(), nullable=False),
        sa.Column('mes_periodo', sa.Integer(), nullable=False),
        sa.Column('anio_periodo', sa.Integer(), nullable=False),
        sa.Column('tipo', sa.Enum('normal', 'refacturacion', name='lote_tipo'), nullable=False, server_default='normal'),
        sa.Column('snap_origen_id', sa.Integer(), nullable=True),
        sa.Column('estado', sa.Enum('A', 'C', 'L', name='lote_estado'), nullable=False, server_default='A'),
        sa.Column('pago_id', sa.Integer(), nullable=True),
        sa.Column('total_debitos', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
        sa.Column('total_creditos', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
        *_AUDIT,
        sa.ForeignKeyConstraint(['obra_social_id'], ['obras_sociales.NRO_OBRASOCIAL']),
        sa.ForeignKeyConstraint(['snap_origen_id'], ['lote_ajuste.id']),
        sa.ForeignKeyConstraint(['pago_id'], ['pago.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('snap_origen_id', name='uq_lote_origen'),
    )
    op.create_index('idx_lote_os_per', 'lote_ajuste', ['obra_social_id', 'mes_periodo', 'anio_periodo'])
    op.create_index('idx_lote_pago', 'lote_ajuste', ['pago_id'])

    # ============================================================
    # 8. Crear tabla ajuste (antes debito_credito)
    # ============================================================
    op.create_table(
        'ajuste',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('lote_id', sa.Integer(), nullable=False),
        sa.Column('tipo', sa.Enum('d', 'c', name='ajuste_tipo'), nullable=False),
        sa.Column('id_atencion', sa.Integer(), nullable=True),
        sa.Column('medico_id', sa.Integer(), nullable=False),
        sa.Column('obra_social_id', sa.Integer(), nullable=False),
        sa.Column('monto', sa.DECIMAL(14, 2), nullable=False),
        sa.Column('observacion', sa.String(255), nullable=True),
        sa.Column('origen', sa.Enum('manual', 'importado', name='ajuste_origen'), nullable=False, server_default='manual'),
        sa.Column('ext_id', sa.Integer(), nullable=True),
        *_AUDIT,
        sa.ForeignKeyConstraint(['lote_id'], ['lote_ajuste.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['id_atencion'], ['guardar_atencion.ID'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['medico_id'], ['listado_medico.ID']),
        sa.ForeignKeyConstraint(['obra_social_id'], ['obras_sociales.NRO_OBRASOCIAL']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('ext_id', name='uq_ajuste_ext_id'),
    )
    op.create_index('idx_ajuste_lote', 'ajuste', ['lote_id'])
    op.create_index('idx_ajuste_medico', 'ajuste', ['medico_id'])
    op.create_index('idx_ajuste_atencion', 'ajuste', ['id_atencion'])

    # ============================================================
    # 9. Crear tabla deducciones (con pago_id en lugar de resumen_id/anio/mes)
    # ============================================================
    op.create_table(
        'deducciones',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('medico_id', sa.Integer(), nullable=False),
        sa.Column('pago_id', sa.Integer(), nullable=False),
        sa.Column('descuento_id', sa.Integer(), nullable=True),
        sa.Column('calculado_total', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
        sa.Column('porcentaje_aplicado', sa.DECIMAL(10, 2), nullable=False, server_default='0.00'),
        sa.Column('monto_aplicado', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
        sa.Column('pagado', sa.Boolean(), nullable=False, server_default='0'),
        *_AUDIT,
        sa.ForeignKeyConstraint(['medico_id'], ['listado_medico.ID']),
        sa.ForeignKeyConstraint(['pago_id'], ['pago.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['descuento_id'], ['descuentos.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('pago_id', 'medico_id', 'descuento_id', name='uq_ded_pago_med_desc'),
    )
    op.create_index('idx_ded_pago_med', 'deducciones', ['pago_id', 'medico_id'])
    op.create_index('idx_ded_medico', 'deducciones', ['medico_id'])

    # ============================================================
    # 10. Crear tabla deduccion_aplicacion (con pago_id)
    # ============================================================
    op.create_table(
        'deduccion_aplicacion',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('pago_id', sa.Integer(), nullable=False),
        sa.Column('medico_id', sa.Integer(), nullable=False),
        sa.Column('concepto_tipo', sa.Enum('desc', 'esp', name='ded_apl_tipo'), nullable=False),
        sa.Column('concepto_id', sa.Integer(), nullable=False),
        sa.Column('aplicado', sa.DECIMAL(14, 2), nullable=False, server_default='0.00'),
        *_AUDIT,
        sa.ForeignKeyConstraint(['pago_id'], ['pago.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['medico_id'], ['listado_medico.ID']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('pago_id', 'medico_id', 'concepto_tipo', 'concepto_id', name='uq_dedapli_pago_med_conc'),
    )
    op.create_index('idx_apl_pago_med', 'deduccion_aplicacion', ['pago_id', 'medico_id'])
    op.create_index('idx_apl_conc', 'deduccion_aplicacion', ['concepto_tipo', 'concepto_id'])


def downgrade() -> None:
    raise NotImplementedError(
        "Esta migración no tiene downgrade: los datos de las tablas antiguas fueron eliminados "
        "intencionalmente al hacer upgrade. No es posible revertir."
    )
