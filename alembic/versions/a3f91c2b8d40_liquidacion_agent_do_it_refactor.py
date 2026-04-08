"""liquidacion agent_do_it refactor

Revision ID: a3f91c2b8d40
Revises: 67154838144e
Create Date: 2026-02-27 00:00:00.000000

Cambios incluidos:
- Liquidacion.cierre_timestamp: String(25) → DateTime
- DetalleLiquidacion.prestacion_id: String(16) → Integer FK a guardar_atencion.ID
- DetalleLiquidacion: eliminar columna debito_credito_id
- Debito_Credito: eliminar periodo String(7), agregar anio+mes Integer,
                  agregar detalle_liquidacion_id FK
- Deduccion: agregar resumen_id FK, cambiar unique constraint
- DeduccionAplicacion: reemplazar anio+mes+descuento_id → resumen_id+concepto_tipo+concepto_id
- Crear tabla liquidacion_medico
- Crear tabla recibo
- Crear tabla recibo_item
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = 'a3f91c2b8d40'
down_revision: Union[str, Sequence[str], None] = '67154838144e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----------------------------------------------------------------
    # 1) Liquidacion: cierre_timestamp String → DateTime
    # ----------------------------------------------------------------
    op.alter_column(
        'liquidacion', 'cierre_timestamp',
        existing_type=sa.String(length=25),
        type_=sa.DateTime(),
        existing_nullable=True,
        nullable=True,
    )

    # ----------------------------------------------------------------
    # 2) DetalleLiquidacion: prestacion_id String(16) → Integer + FK
    # ----------------------------------------------------------------
    # Primero eliminamos el unique constraint que incluye prestacion_id
    op.drop_constraint('uq_det_prest_en_liq', 'detalle_liquidacion', type_='unique')
    op.drop_index('idx_det_prest', 'detalle_liquidacion')

    # Cambiar tipo de columna (datos deben ser numéricos)
    op.alter_column(
        'detalle_liquidacion', 'prestacion_id',
        existing_type=sa.String(length=16),
        type_=sa.Integer(),
        existing_nullable=False,
        nullable=False,
    )

    # Agregar FK a guardar_atencion
    op.create_foreign_key(
        'fk_det_prestacion_atencion',
        'detalle_liquidacion', 'guardar_atencion',
        ['prestacion_id'], ['ID'],
        ondelete='RESTRICT',
    )

    # Recrear unique e índice
    op.create_unique_constraint(
        'uq_det_prest_en_liq',
        'detalle_liquidacion',
        ['prestacion_id', 'liquidacion_id', 'medico_id'],
    )
    op.create_index('idx_det_prest', 'detalle_liquidacion', ['prestacion_id'])

    # ----------------------------------------------------------------
    # 3) DetalleLiquidacion: eliminar debito_credito_id
    # ----------------------------------------------------------------
    # Primero eliminamos FK si existe
    try:
        op.drop_constraint(
            'detalle_liquidacion_ibfk_2',  # nombre puede variar según DB
            'detalle_liquidacion',
            type_='foreignkey',
        )
    except Exception:
        pass
    try:
        op.drop_index('ix_detalle_liquidacion_debito_credito_id', 'detalle_liquidacion')
    except Exception:
        pass
    op.drop_column('detalle_liquidacion', 'debito_credito_id')

    # ----------------------------------------------------------------
    # 4) Debito_Credito: reemplazar periodo por anio+mes, agregar detalle_liquidacion_id
    # ----------------------------------------------------------------
    # Agregar nuevas columnas
    op.add_column('debito_credito', sa.Column('anio', sa.Integer(), nullable=True))
    op.add_column('debito_credito', sa.Column('mes', sa.Integer(), nullable=True))
    op.add_column(
        'debito_credito',
        sa.Column(
            'detalle_liquidacion_id', sa.Integer(),
            sa.ForeignKey('detalle_liquidacion.id', ondelete='CASCADE'),
            nullable=True,
        ),
    )

    # Poblar anio/mes desde periodo (formato YYYY-MM)
    op.execute(
        """
        UPDATE debito_credito
        SET
          anio = CAST(SUBSTRING(periodo, 1, 4) AS UNSIGNED),
          mes  = CAST(SUBSTRING(periodo, 6, 2) AS UNSIGNED)
        WHERE periodo IS NOT NULL AND periodo REGEXP '^[0-9]{4}-[0-9]{1,2}$'
        """
    )

    # Hacer anio/mes not null
    op.alter_column('debito_credito', 'anio', existing_type=sa.Integer(), nullable=False)
    op.alter_column('debito_credito', 'mes', existing_type=sa.Integer(), nullable=False)

    # Crear índices
    op.create_index('idx_dc_anio_mes', 'debito_credito', ['anio', 'mes'])
    op.create_index('idx_dc_atencion_os', 'debito_credito', ['id_atencion', 'obra_social_id'])
    op.create_index(
        'ix_debito_credito_detalle_liquidacion_id',
        'debito_credito',
        ['detalle_liquidacion_id'],
    )

    # Eliminar columna periodo y su índice
    try:
        op.drop_index('ix_debito_credito_periodo', 'debito_credito')
    except Exception:
        pass
    op.drop_column('debito_credito', 'periodo')

    # ----------------------------------------------------------------
    # 5) Deduccion: agregar resumen_id, cambiar unique constraint
    # ----------------------------------------------------------------
    op.add_column(
        'deducciones',
        sa.Column(
            'resumen_id', sa.Integer(),
            sa.ForeignKey('liquidacion_resumen.id', ondelete='RESTRICT'),
            nullable=True,  # nullable primero para data migration
        ),
    )
    op.create_index('idx_ded_resumen_med', 'deducciones', ['resumen_id', 'medico_id'])
    try:
        op.create_index('uq_ded_resumen_med_desc', 'deducciones', ['resumen_id', 'medico_id', 'descuento_id'], unique=True)
    except Exception:
        pass

    # Eliminar uniques redundantes anteriores
    try:
        op.drop_constraint('uq_ded_med_per_desc', 'deducciones', type_='unique')
    except Exception:
        pass
    try:
        op.drop_constraint('uq_deducc_med_desc_period', 'deducciones', type_='unique')
    except Exception:
        pass

    # Nuevo unique (resumen_id puede tener NULL si hay datos previos, lo agregamos después)
    # Por ahora no creamos el unique hasta que los datos estén migrados

    # ----------------------------------------------------------------
    # 6) DeduccionAplicacion: reestructurar
    #    anio+mes+descuento_id → resumen_id+concepto_tipo+concepto_id
    # ----------------------------------------------------------------
    # Agregar nuevas columnas
    op.add_column(
        'deduccion_aplicacion',
        sa.Column(
            'resumen_id', sa.Integer(),
            sa.ForeignKey('liquidacion_resumen.id', ondelete='RESTRICT'),
            nullable=True,
        ),
    )
    op.add_column(
        'deduccion_aplicacion',
        sa.Column(
            'concepto_tipo',
            sa.Enum('desc', 'esp', name='ded_apl_tipo'),
            nullable=True,
        ),
    )
    op.add_column(
        'deduccion_aplicacion',
        sa.Column('concepto_id', sa.Integer(), nullable=True),
    )

    # Poblar concepto_id desde descuento_id
    op.execute(
        """
        UPDATE deduccion_aplicacion
        SET concepto_tipo = 'desc',
            concepto_id   = descuento_id
        WHERE descuento_id IS NOT NULL
        """
    )

    # Crear índices
    op.create_index('idx_apl_res_med', 'deduccion_aplicacion', ['resumen_id', 'medico_id'])
    op.create_index('idx_apl_conc', 'deduccion_aplicacion', ['concepto_tipo', 'concepto_id'])
    try:
        op.create_index('uq_apl_res_med_conc', 'deduccion_aplicacion', ['resumen_id', 'medico_id', 'concepto_tipo', 'concepto_id'], unique=True)
    except Exception:
        pass

    # Eliminar columnas antiguas de deduccion_aplicacion
    try:
        op.drop_constraint('uq_dedapli_med_desc_period', 'deduccion_aplicacion', type_='unique')
    except Exception:
        pass
    try:
        op.drop_index('idx_apl_per_med', 'deduccion_aplicacion')
    except Exception:
        pass
    try:
        op.drop_column('deduccion_aplicacion', 'anio')
    except Exception:
        pass
    try:
        op.drop_column('deduccion_aplicacion', 'mes')
    except Exception:
        pass
    try:
        op.drop_column('deduccion_aplicacion', 'descuento_id')
    except Exception:
        pass

    # ----------------------------------------------------------------
    # 7) Crear tabla liquidacion_medico
    # ----------------------------------------------------------------
    op.create_table(
        'liquidacion_medico',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('resumen_id', sa.Integer(), nullable=False),
        sa.Column('medico_id', mysql.INTEGER(display_width=11), nullable=False),
        sa.Column('bruto', sa.DECIMAL(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('debitos', sa.DECIMAL(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('creditos', sa.DECIMAL(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('reconocido', sa.DECIMAL(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('deducciones', sa.DECIMAL(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('neto_a_pagar', sa.DECIMAL(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column(
            'estado',
            sa.Enum('pendiente', 'liquidado', 'pagado', name='liqmed_estado'),
            nullable=False,
            server_default='pendiente',
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by_user', mysql.INTEGER(display_width=11), nullable=True),
        sa.ForeignKeyConstraint(['created_by_user'], ['listado_medico.ID']),
        sa.ForeignKeyConstraint(['medico_id'], ['listado_medico.ID'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['resumen_id'], ['liquidacion_resumen.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('resumen_id', 'medico_id', name='uq_liqmed_res_med'),
    )
    op.create_index('idx_liqmed_res_med', 'liquidacion_medico', ['resumen_id', 'medico_id'])
    op.create_index('ix_liquidacion_medico_resumen_id', 'liquidacion_medico', ['resumen_id'])
    op.create_index('ix_liquidacion_medico_medico_id', 'liquidacion_medico', ['medico_id'])

    # ----------------------------------------------------------------
    # 8) Crear tabla recibo
    # ----------------------------------------------------------------
    op.create_table(
        'recibo',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('nro_recibo', sa.String(length=30), nullable=False),
        sa.Column('resumen_id', sa.Integer(), nullable=False),
        sa.Column('medico_id', mysql.INTEGER(display_width=11), nullable=False),
        sa.Column('total_neto', sa.DECIMAL(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('emision_timestamp', sa.DateTime(), nullable=True),
        sa.Column(
            'estado',
            sa.Enum('emitido', 'anulado', 'pagado', name='recibo_estado'),
            nullable=False,
            server_default='emitido',
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by_user', mysql.INTEGER(display_width=11), nullable=True),
        sa.ForeignKeyConstraint(['created_by_user'], ['listado_medico.ID']),
        sa.ForeignKeyConstraint(['medico_id'], ['listado_medico.ID'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['resumen_id'], ['liquidacion_resumen.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('resumen_id', 'medico_id', name='uq_recibo_res_med'),
    )
    op.create_index('ix_recibo_nro_recibo', 'recibo', ['nro_recibo'])
    op.create_index('idx_recibo_res', 'recibo', ['resumen_id'])
    op.create_index('idx_recibo_med', 'recibo', ['medico_id'])

    # ----------------------------------------------------------------
    # 9) Crear tabla recibo_item
    # ----------------------------------------------------------------
    op.create_table(
        'recibo_item',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('recibo_id', sa.Integer(), nullable=False),
        sa.Column('liquidacion_id', sa.Integer(), nullable=False),
        sa.Column('concepto', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('importe', sa.DECIMAL(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by_user', mysql.INTEGER(display_width=11), nullable=True),
        sa.ForeignKeyConstraint(['created_by_user'], ['listado_medico.ID']),
        sa.ForeignKeyConstraint(['liquidacion_id'], ['liquidacion.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['recibo_id'], ['recibo.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_recibo_item_recibo_id', 'recibo_item', ['recibo_id'])
    op.create_index('ix_recibo_item_liquidacion_id', 'recibo_item', ['liquidacion_id'])


def downgrade() -> None:
    # ----------------------------------------------------------------
    # Revertir en orden inverso
    # ----------------------------------------------------------------

    # 9) Eliminar recibo_item
    op.drop_table('recibo_item')

    # 8) Eliminar recibo
    op.drop_table('recibo')

    # 7) Eliminar liquidacion_medico
    op.drop_table('liquidacion_medico')

    # 6) Revertir DeduccionAplicacion
    op.add_column('deduccion_aplicacion', sa.Column('anio', sa.Integer(), nullable=True))
    op.add_column('deduccion_aplicacion', sa.Column('mes', sa.Integer(), nullable=True))
    op.add_column('deduccion_aplicacion', sa.Column('descuento_id', sa.Integer(), nullable=True))
    op.execute(
        "UPDATE deduccion_aplicacion SET descuento_id = concepto_id WHERE concepto_tipo = 'desc'"
    )
    try:
        op.drop_index('uq_apl_res_med_conc', 'deduccion_aplicacion')
        op.drop_index('idx_apl_conc', 'deduccion_aplicacion')
        op.drop_index('idx_apl_res_med', 'deduccion_aplicacion')
        op.drop_column('deduccion_aplicacion', 'concepto_id')
        op.drop_column('deduccion_aplicacion', 'concepto_tipo')
        op.drop_column('deduccion_aplicacion', 'resumen_id')
    except Exception:
        pass

    # 5) Revertir Deduccion
    try:
        op.drop_index('uq_ded_resumen_med_desc', 'deducciones')
        op.drop_index('idx_ded_resumen_med', 'deducciones')
        op.drop_column('deducciones', 'resumen_id')
    except Exception:
        pass

    # 4) Revertir Debito_Credito
    op.add_column('debito_credito', sa.Column('periodo', sa.String(length=7), nullable=True))
    op.execute(
        "UPDATE debito_credito SET periodo = CONCAT(LPAD(anio, 4, '0'), '-', LPAD(mes, 2, '0'))"
    )
    try:
        op.drop_index('idx_dc_anio_mes', 'debito_credito')
        op.drop_index('idx_dc_atencion_os', 'debito_credito')
        op.drop_index('ix_debito_credito_detalle_liquidacion_id', 'debito_credito')
        op.drop_constraint('fk_dc_detalle', 'debito_credito', type_='foreignkey')
    except Exception:
        pass
    op.drop_column('debito_credito', 'detalle_liquidacion_id')
    op.drop_column('debito_credito', 'mes')
    op.drop_column('debito_credito', 'anio')
    op.create_index('ix_debito_credito_periodo', 'debito_credito', ['periodo'])

    # 3) Revertir DetalleLiquidacion: volver a agregar debito_credito_id
    op.add_column(
        'detalle_liquidacion',
        sa.Column(
            'debito_credito_id', sa.Integer(),
            nullable=True,
        ),
    )

    # 2) Revertir prestacion_id Integer → String
    try:
        op.drop_constraint('fk_det_prestacion_atencion', 'detalle_liquidacion', type_='foreignkey')
        op.drop_constraint('uq_det_prest_en_liq', 'detalle_liquidacion', type_='unique')
        op.drop_index('idx_det_prest', 'detalle_liquidacion')
    except Exception:
        pass
    op.alter_column(
        'detalle_liquidacion', 'prestacion_id',
        existing_type=sa.Integer(),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
    op.create_unique_constraint(
        'uq_det_prest_en_liq',
        'detalle_liquidacion',
        ['prestacion_id', 'liquidacion_id', 'medico_id'],
    )
    op.create_index('idx_det_prest', 'detalle_liquidacion', ['prestacion_id'])

    # 1) Revertir cierre_timestamp DateTime → String
    op.alter_column(
        'liquidacion', 'cierre_timestamp',
        existing_type=sa.DateTime(),
        type_=sa.String(length=25),
        existing_nullable=True,
        nullable=True,
    )
