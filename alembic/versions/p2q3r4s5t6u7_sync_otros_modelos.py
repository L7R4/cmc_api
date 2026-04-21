"""sync_otros_modelos

Revision ID: p2q3r4s5t6u7
Revises: o1p2q3r4s5t6
Create Date: 2026-04-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = 'p2q3r4s5t6u7'
down_revision: Union[str, Sequence[str], None] = 'o1p2q3r4s5t6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTA: Los pasos 1-6 ya fueron aplicados en una ejecución anterior parcial:
    #  - Tablas legacy eliminadas: debito_credito, deduccion_saldo,
    #    deducciones_colegio, liquidacion_resumen
    #  - Tablas nuevas ya existían: pago, deducciones, lote_ajuste, pago_medico,
    #    socio_descuento, ajuste, recibo
    #  - Columnas de deduccion_aplicacion, descuentos, detalle_liquidacion,
    #    liquidacion ya refactorizadas
    # Solo resta: índices en listado_medico + ajuste menor en periodos.
    # NOTA 2: Los ALTER NOT NULL de listado_medico se omiten — datos con NULL
    # existentes impiden la conversión.

    # ── listado_medico: solo índices (NOT NULL skipped por datos legacy) ───
    op.create_index('CATEGORIA', 'listado_medico', ['CATEGORIA'], unique=False)
    op.create_index('NRO_ESPECIALIDAD', 'listado_medico', ['NRO_ESPECIALIDAD'], unique=False)
    op.create_index('NRO_ESPECIALIDAD2', 'listado_medico', ['NRO_ESPECIALIDAD2'], unique=False)
    op.create_index('NRO_ESPECIALIDAD3', 'listado_medico', ['NRO_ESPECIALIDAD3'], unique=False)
    op.create_index('NRO_ESPECIALIDAD4', 'listado_medico', ['NRO_ESPECIALIDAD4'], unique=False)
    op.create_index('NRO_ESPECIALIDAD5', 'listado_medico', ['NRO_ESPECIALIDAD5'], unique=False)

    # ── periodos ──────────────────────────────────────────────────────────
    op.alter_column('periodos', 'USUARIO',
        existing_type=mysql.INTEGER(display_width=11),
        type_=mysql.INTEGER(display_width=10),
        existing_nullable=False,
        existing_server_default=sa.text("'0'"))
    op.drop_column('periodos_doctor', 'USUARIO')


def _upgrade_already_applied() -> None:
    """HISTÓRICO — pasos ya aplicados, no se ejecutan. Conservado para referencia."""
    # ── Paso 1: liberar FKs que apuntan a tablas que vamos a eliminar ─────
    # detalle_liquidacion → debito_credito
    op.drop_constraint(op.f('detalle_liquidacion_ibfk_2'), 'detalle_liquidacion', type_='foreignkey')
    # detalle_liquidacion self-ref prev_detalle_id
    op.drop_constraint(op.f('detalle_liquidacion_ibfk_4'), 'detalle_liquidacion', type_='foreignkey')
    # deduccion_aplicacion → liquidacion_resumen
    op.drop_constraint(op.f('deduccion_aplicacion_ibfk_2'), 'deduccion_aplicacion', type_='foreignkey')
    op.drop_constraint(op.f('deduccion_aplicacion_ibfk_3'), 'deduccion_aplicacion', type_='foreignkey')
    # liquidacion → liquidacion_resumen
    op.drop_constraint(op.f('liquidacion_ibfk_2'), 'liquidacion', type_='foreignkey')

    # ── Paso 2: eliminar tablas legacy (FKs propias primero, luego índices, luego tabla) ──

    # debito_credito
    op.drop_constraint(op.f('debito_credito_ibfk_1'), 'debito_credito', type_='foreignkey')
    op.drop_constraint(op.f('debito_credito_ibfk_2'), 'debito_credito', type_='foreignkey')
    op.drop_constraint(op.f('debito_credito_ibfk_3'), 'debito_credito', type_='foreignkey')
    op.drop_index(op.f('ix_debito_credito_created_by_user'), table_name='debito_credito')
    op.drop_index(op.f('ix_debito_credito_id_atencion'), table_name='debito_credito')
    op.drop_index(op.f('ix_debito_credito_obra_social_id'), table_name='debito_credito')
    op.drop_index(op.f('ix_debito_credito_periodo'), table_name='debito_credito')
    op.drop_table('debito_credito')

    # deduccion_saldo
    op.drop_constraint(op.f('deduccion_saldo_ibfk_1'), 'deduccion_saldo', type_='foreignkey')
    op.drop_constraint(op.f('deduccion_saldo_ibfk_2'), 'deduccion_saldo', type_='foreignkey')
    op.drop_index(op.f('ix_deduccion_saldo_concepto_id'), table_name='deduccion_saldo')
    op.drop_index(op.f('ix_deduccion_saldo_concepto_tipo'), table_name='deduccion_saldo')
    op.drop_index(op.f('ix_deduccion_saldo_created_by_user'), table_name='deduccion_saldo')
    op.drop_index(op.f('ix_deduccion_saldo_medico_id'), table_name='deduccion_saldo')
    op.drop_index(op.f('uq_saldo_med_concepto'), table_name='deduccion_saldo')
    op.drop_table('deduccion_saldo')

    # deducciones_colegio (referencia a liquidacion_resumen que eliminamos después)
    op.drop_constraint(op.f('deducciones_colegio_ibfk_1'), 'deducciones_colegio', type_='foreignkey')
    op.drop_constraint(op.f('deducciones_colegio_ibfk_2'), 'deducciones_colegio', type_='foreignkey')
    op.drop_constraint(op.f('deducciones_colegio_ibfk_3'), 'deducciones_colegio', type_='foreignkey')
    op.drop_constraint(op.f('deducciones_colegio_ibfk_4'), 'deducciones_colegio', type_='foreignkey')
    op.drop_constraint(op.f('deducciones_colegio_ibfk_5'), 'deducciones_colegio', type_='foreignkey')
    op.drop_index(op.f('idx_med_res'), table_name='deducciones_colegio')
    op.drop_index(op.f('ix_deducciones_colegio_created_by_user'), table_name='deducciones_colegio')
    op.drop_index(op.f('ix_deducciones_colegio_descuento_id'), table_name='deducciones_colegio')
    op.drop_index(op.f('ix_deducciones_colegio_especialidad_id'), table_name='deducciones_colegio')
    op.drop_index(op.f('ix_deducciones_colegio_medico_id'), table_name='deducciones_colegio')
    op.drop_index(op.f('ix_deducciones_colegio_resumen_id'), table_name='deducciones_colegio')
    op.drop_index(op.f('uq_med_res_desc'), table_name='deducciones_colegio')
    op.drop_index(op.f('uq_med_res_esp2'), table_name='deducciones_colegio')
    op.drop_table('deducciones_colegio')

    # liquidacion_resumen (ahora no tiene referencias externas pendientes)
    op.drop_constraint(op.f('liquidacion_resumen_ibfk_1'), 'liquidacion_resumen', type_='foreignkey')
    op.drop_index(op.f('ix_liquidacion_resumen_created_by_user'), table_name='liquidacion_resumen')
    op.drop_index(op.f('uq_liqres_anio_mes'), table_name='liquidacion_resumen')
    op.drop_table('liquidacion_resumen')

    # ── Paso 3: deduccion_aplicacion: refactor columnas e índices ─────────
    op.add_column('deduccion_aplicacion', sa.Column('pago_id', sa.Integer(), nullable=True))
    op.add_column('deduccion_aplicacion', sa.Column('deduccion_id', sa.Integer(), nullable=False))
    op.drop_index(op.f('ix_deduccion_aplicacion_concepto_id'), table_name='deduccion_aplicacion')
    op.drop_index(op.f('ix_deduccion_aplicacion_concepto_tipo'), table_name='deduccion_aplicacion')
    op.drop_index(op.f('ix_deduccion_aplicacion_medico_id'), table_name='deduccion_aplicacion')
    op.drop_index(op.f('ix_deduccion_aplicacion_resumen_id'), table_name='deduccion_aplicacion')
    op.drop_index(op.f('uq_apl_res_med_concepto'), table_name='deduccion_aplicacion')
    op.create_index('idx_apl_deduccion', 'deduccion_aplicacion', ['deduccion_id'], unique=False)
    op.create_index('idx_apl_pago', 'deduccion_aplicacion', ['pago_id'], unique=False)
    op.create_index(op.f('ix_deduccion_aplicacion_deduccion_id'), 'deduccion_aplicacion', ['deduccion_id'], unique=False)
    op.create_index(op.f('ix_deduccion_aplicacion_pago_id'), 'deduccion_aplicacion', ['pago_id'], unique=False)
    op.create_unique_constraint('uq_dedapli_pago_ded', 'deduccion_aplicacion', ['pago_id', 'deduccion_id'])
    op.create_foreign_key(None, 'deduccion_aplicacion', 'pago', ['pago_id'], ['id'], ondelete='RESTRICT')
    op.create_foreign_key(None, 'deduccion_aplicacion', 'deducciones', ['deduccion_id'], ['id'], ondelete='RESTRICT')
    op.drop_column('deduccion_aplicacion', 'concepto_id')
    op.drop_column('deduccion_aplicacion', 'resumen_id')
    op.drop_column('deduccion_aplicacion', 'medico_id')
    op.drop_column('deduccion_aplicacion', 'concepto_tipo')

    # ── Paso 4: descuentos ────────────────────────────────────────────────
    op.add_column('descuentos', sa.Column('aplica_a_todos', sa.Boolean(), server_default='0', nullable=False))

    # ── Paso 5: detalle_liquidacion: refactor columnas, tipo prestacion_id ──
    op.add_column('detalle_liquidacion', sa.Column('honorarios', sa.DECIMAL(precision=14, scale=2), server_default='0.00', nullable=False))
    op.add_column('detalle_liquidacion', sa.Column('gastos', sa.DECIMAL(precision=14, scale=2), server_default='0.00', nullable=False))
    op.add_column('detalle_liquidacion', sa.Column('importe_total', sa.DECIMAL(precision=14, scale=2), server_default='0.00', nullable=False))
    op.alter_column('detalle_liquidacion', 'prestacion_id',
        existing_type=mysql.VARCHAR(collation='utf8mb4_unicode_ci', length=16),
        type_=mysql.INTEGER(display_width=11),
        existing_nullable=False)
    op.drop_index(op.f('idx_det_os_liq_med'), table_name='detalle_liquidacion')
    op.drop_index(op.f('idx_det_prest'), table_name='detalle_liquidacion')
    op.drop_index(op.f('ix_detalle_liquidacion_debito_credito_id'), table_name='detalle_liquidacion')
    op.create_index(op.f('ix_detalle_liquidacion_prestacion_id'), 'detalle_liquidacion', ['prestacion_id'], unique=False)
    op.create_foreign_key(None, 'detalle_liquidacion', 'guardar_atencion', ['prestacion_id'], ['ID'], ondelete='RESTRICT')
    op.drop_column('detalle_liquidacion', 'prev_detalle_id')
    op.drop_column('detalle_liquidacion', 'importe')
    op.drop_column('detalle_liquidacion', 'debito_credito_id')

    # ── Paso 6: liquidacion: refactor columnas, agregar FK a pago ─────────
    op.add_column('liquidacion', sa.Column('pago_id', sa.Integer(), nullable=False))
    op.add_column('liquidacion', sa.Column('nro_factura', sa.String(length=30), nullable=True))
    op.add_column('liquidacion', sa.Column('total_honorarios', sa.DECIMAL(precision=14, scale=2), server_default='0.00', nullable=False))
    op.add_column('liquidacion', sa.Column('total_gastos', sa.DECIMAL(precision=14, scale=2), server_default='0.00', nullable=False))
    op.add_column('liquidacion', sa.Column('total_creditos', sa.DECIMAL(precision=14, scale=2), nullable=False))
    op.drop_index(op.f('idx_liq_os_per_version'), table_name='liquidacion')
    op.drop_index(op.f('idx_liq_res_os_per'), table_name='liquidacion')
    op.drop_index(op.f('ix_liquidacion_estado'), table_name='liquidacion')
    op.drop_index(op.f('uq_liq_res_os_per_v2'), table_name='liquidacion')
    op.create_index('idx_liq_pago_os_per', 'liquidacion', ['pago_id', 'obra_social_id', 'mes_periodo', 'anio_periodo'], unique=False)
    op.create_index(op.f('ix_liquidacion_pago_id'), 'liquidacion', ['pago_id'], unique=False)
    op.create_unique_constraint('uq_liq_pago_os_per', 'liquidacion', ['pago_id', 'obra_social_id', 'mes_periodo', 'anio_periodo'])
    op.create_foreign_key(None, 'liquidacion', 'pago', ['pago_id'], ['id'])
    op.drop_column('liquidacion', 'resumen_id')
    op.drop_column('liquidacion', 'estado')
    op.drop_column('liquidacion', 'cierre_timestamp')
    op.drop_column('liquidacion', 'nro_liquidacion')
    op.drop_column('liquidacion', 'version')

    # ── listado_medico: nullable → NOT NULL y correcciones de tipo ────────
    op.alter_column('listado_medico', 'NRO_ESPECIALIDAD', existing_type=mysql.INTEGER(display_width=11), nullable=False)
    op.alter_column('listado_medico', 'NRO_ESPECIALIDAD2', existing_type=mysql.INTEGER(display_width=11), nullable=False)
    op.alter_column('listado_medico', 'NRO_ESPECIALIDAD3', existing_type=mysql.INTEGER(display_width=11), nullable=False)
    op.alter_column('listado_medico', 'NRO_ESPECIALIDAD4', existing_type=mysql.INTEGER(display_width=11), nullable=False)
    op.alter_column('listado_medico', 'NRO_ESPECIALIDAD5', existing_type=mysql.INTEGER(display_width=11), nullable=False)
    op.alter_column('listado_medico', 'NRO_SOCIO', existing_type=mysql.INTEGER(display_width=11), nullable=False)
    op.alter_column('listado_medico', 'NOMBRE', existing_type=mysql.VARCHAR(length=40), nullable=False)
    op.alter_column('listado_medico', 'DOMICILIO_CONSULTA',
        existing_type=mysql.VARCHAR(charset='utf8', collation='utf8_spanish_ci', length=103),
        type_=sa.String(length=100, collation='utf8_spanish2_ci'), nullable=False)
    op.alter_column('listado_medico', 'TELEFONO_CONSULTA', existing_type=mysql.VARCHAR(length=25), nullable=False)
    op.alter_column('listado_medico', 'MATRICULA_PROV', existing_type=mysql.INTEGER(display_width=11), nullable=False)
    op.alter_column('listado_medico', 'MATRICULA_NAC', existing_type=mysql.INTEGER(display_width=11), nullable=False)
    op.alter_column('listado_medico', 'DOMICILIO_PARTICULAR',
        existing_type=mysql.VARCHAR(length=66),
        type_=sa.String(length=100, collation='utf8_spanish2_ci'), nullable=False)
    op.alter_column('listado_medico', 'TELE_PARTICULAR',
        existing_type=mysql.VARCHAR(length=11),
        type_=sa.String(length=15, collation='utf8_spanish2_ci'), nullable=False)
    op.alter_column('listado_medico', 'CELULAR_PARTICULAR',
        existing_type=mysql.VARCHAR(length=14),
        type_=sa.String(length=15, collation='utf8_spanish2_ci'), nullable=False)
    op.alter_column('listado_medico', 'MAIL_PARTICULAR', existing_type=mysql.VARCHAR(length=50), nullable=False)
    op.alter_column('listado_medico', 'SEXO', existing_type=mysql.VARCHAR(length=1), nullable=False)
    op.alter_column('listado_medico', 'TIPO_DOC', existing_type=mysql.VARCHAR(length=3), nullable=False)
    op.alter_column('listado_medico', 'DOCUMENTO',
        existing_type=mysql.INTEGER(display_width=11),
        type_=sa.String(length=8, collation='utf8_spanish2_ci'), nullable=False)
    op.alter_column('listado_medico', 'CUIT', existing_type=mysql.VARCHAR(length=12), nullable=False)
    op.alter_column('listado_medico', 'ANSSAL', existing_type=mysql.INTEGER(display_width=11), nullable=False)
    op.alter_column('listado_medico', 'MALAPRAXIS',
        existing_type=mysql.VARCHAR(length=69),
        type_=sa.String(length=100, collation='utf8_spanish2_ci'), nullable=False)
    op.alter_column('listado_medico', 'MONOTRIBUTISTA', existing_type=mysql.VARCHAR(length=2), nullable=False)
    op.alter_column('listado_medico', 'FACTURA', existing_type=mysql.VARCHAR(length=2), nullable=False)
    op.alter_column('listado_medico', 'COBERTURA', existing_type=mysql.INTEGER(display_width=11), nullable=False)
    op.alter_column('listado_medico', 'PROVINCIA',
        existing_type=mysql.VARCHAR(length=23),
        type_=sa.String(length=25, collation='utf8_spanish2_ci'), nullable=False)
    op.alter_column('listado_medico', 'CODIGO_POSTAL',
        existing_type=mysql.INTEGER(display_width=11),
        type_=sa.String(length=15, collation='utf8_spanish2_ci'), nullable=False)
    op.alter_column('listado_medico', 'VITALICIO', existing_type=mysql.VARCHAR(length=1), nullable=False)
    op.alter_column('listado_medico', 'OBSERVACION', existing_type=mysql.VARCHAR(length=200), nullable=False)
    op.alter_column('listado_medico', 'CATEGORIA', existing_type=mysql.VARCHAR(length=1), nullable=False)
    op.alter_column('listado_medico', 'EXISTE', existing_type=mysql.VARCHAR(length=1), nullable=False)
    op.alter_column('listado_medico', 'NRO_ESPECIALIDAD6', existing_type=mysql.INTEGER(display_width=11), nullable=False)
    op.alter_column('listado_medico', 'EXCEP_DESDE',
        existing_type=mysql.VARCHAR(length=8),
        type_=sa.String(length=6, collation='utf8_spanish2_ci'), nullable=False)
    op.alter_column('listado_medico', 'EXCEP_HASTA',
        existing_type=mysql.VARCHAR(length=8),
        type_=sa.String(length=6, collation='utf8_spanish2_ci'), nullable=False)
    op.alter_column('listado_medico', 'EXCEP_DESDE2',
        existing_type=mysql.VARCHAR(charset='utf8', collation='utf8_spanish2_ci', length=8),
        type_=sa.String(length=6, collation='utf8_spanish2_ci'), nullable=False)
    op.alter_column('listado_medico', 'EXCEP_HASTA2',
        existing_type=mysql.VARCHAR(charset='utf32', collation='utf32_spanish2_ci', length=8),
        type_=sa.String(length=6, collation='utf8_spanish2_ci'), nullable=False)
    op.alter_column('listado_medico', 'EXCEP_DESDE3',
        existing_type=mysql.VARCHAR(charset='utf8', collation='utf8_spanish2_ci', length=8),
        type_=sa.String(length=6, collation='utf8_spanish2_ci'), nullable=False)
    op.alter_column('listado_medico', 'EXCEP_HASTA3',
        existing_type=mysql.VARCHAR(charset='utf32', collation='utf32_spanish2_ci', length=8),
        type_=sa.String(length=6, collation='utf8_spanish2_ci'), nullable=False)
    op.alter_column('listado_medico', 'INGRESAR',
        existing_type=mysql.VARCHAR(length=1), nullable=False,
        comment='D=DOCTOR / E=EMPLEADOS DEL COLEGIO / A ADMINISTRADOR',
        existing_comment='E=empleados / D=médicos / C=contable / S=superadministrador / A=aduditoria / F=facturación / L=liquidación')
    op.alter_column('listado_medico', 'FECHA_RECIBIDO',
        existing_type=mysql.VARCHAR(length=10), type_=sa.Date(), existing_nullable=True)
    op.alter_column('listado_medico', 'FECHA_MATRICULA',
        existing_type=mysql.VARCHAR(length=10), type_=sa.Date(), existing_nullable=True)
    op.alter_column('listado_medico', 'FECHA_INGRESO',
        existing_type=mysql.VARCHAR(length=10), type_=sa.Date(), existing_nullable=True)
    op.alter_column('listado_medico', 'FECHA_NAC',
        existing_type=mysql.VARCHAR(length=10), type_=sa.Date(), existing_nullable=True)
    op.alter_column('listado_medico', 'VENCIMIENTO_ANSSAL',
        existing_type=mysql.VARCHAR(length=10), type_=sa.Date(), existing_nullable=True)
    op.alter_column('listado_medico', 'VENCIMIENTO_MALAPRAXIS',
        existing_type=mysql.VARCHAR(length=10), type_=sa.Date(), existing_nullable=True)
    op.alter_column('listado_medico', 'VENCIMIENTO_COBERTURA',
        existing_type=mysql.VARCHAR(length=10), type_=sa.Date(), existing_nullable=True)
    op.alter_column('listado_medico', 'FECHA_VITALICIO',
        existing_type=mysql.VARCHAR(length=10), type_=sa.Date(), existing_nullable=True)
    op.create_index('CATEGORIA', 'listado_medico', ['CATEGORIA'], unique=False)
    op.create_index('NRO_ESPECIALIDAD', 'listado_medico', ['NRO_ESPECIALIDAD'], unique=False)
    op.create_index('NRO_ESPECIALIDAD2', 'listado_medico', ['NRO_ESPECIALIDAD2'], unique=False)
    op.create_index('NRO_ESPECIALIDAD3', 'listado_medico', ['NRO_ESPECIALIDAD3'], unique=False)
    op.create_index('NRO_ESPECIALIDAD4', 'listado_medico', ['NRO_ESPECIALIDAD4'], unique=False)
    op.create_index('NRO_ESPECIALIDAD5', 'listado_medico', ['NRO_ESPECIALIDAD5'], unique=False)

    # ── periodos ──────────────────────────────────────────────────────────
    op.alter_column('periodos', 'USUARIO',
        existing_type=mysql.INTEGER(display_width=11),
        type_=mysql.INTEGER(display_width=10),
        existing_nullable=False,
        existing_server_default=sa.text("'0'"))
    op.drop_column('periodos_doctor', 'USUARIO')


def downgrade() -> None:
    op.add_column('periodos_doctor', sa.Column('USUARIO', mysql.INTEGER(display_width=11), server_default=sa.text("'0'"), autoincrement=False, nullable=False))
    op.alter_column('periodos', 'USUARIO',
        existing_type=mysql.INTEGER(display_width=10),
        type_=mysql.INTEGER(display_width=11),
        existing_nullable=False,
        existing_server_default=sa.text("'0'"))
    op.drop_index('NRO_ESPECIALIDAD5', table_name='listado_medico')
    op.drop_index('NRO_ESPECIALIDAD4', table_name='listado_medico')
    op.drop_index('NRO_ESPECIALIDAD3', table_name='listado_medico')
    op.drop_index('NRO_ESPECIALIDAD2', table_name='listado_medico')
    op.drop_index('NRO_ESPECIALIDAD', table_name='listado_medico')
    op.drop_index('CATEGORIA', table_name='listado_medico')
    op.alter_column('listado_medico', 'FECHA_VITALICIO', existing_type=sa.Date(), type_=mysql.VARCHAR(length=10), existing_nullable=True)
    op.alter_column('listado_medico', 'VENCIMIENTO_COBERTURA', existing_type=sa.Date(), type_=mysql.VARCHAR(length=10), existing_nullable=True)
    op.alter_column('listado_medico', 'VENCIMIENTO_MALAPRAXIS', existing_type=sa.Date(), type_=mysql.VARCHAR(length=10), existing_nullable=True)
    op.alter_column('listado_medico', 'VENCIMIENTO_ANSSAL', existing_type=sa.Date(), type_=mysql.VARCHAR(length=10), existing_nullable=True)
    op.alter_column('listado_medico', 'FECHA_NAC', existing_type=sa.Date(), type_=mysql.VARCHAR(length=10), existing_nullable=True)
    op.alter_column('listado_medico', 'FECHA_INGRESO', existing_type=sa.Date(), type_=mysql.VARCHAR(length=10), existing_nullable=True)
    op.alter_column('listado_medico', 'FECHA_MATRICULA', existing_type=sa.Date(), type_=mysql.VARCHAR(length=10), existing_nullable=True)
    op.alter_column('listado_medico', 'FECHA_RECIBIDO', existing_type=sa.Date(), type_=mysql.VARCHAR(length=10), existing_nullable=True)
    op.alter_column('listado_medico', 'INGRESAR', existing_type=mysql.VARCHAR(length=1), nullable=True,
        comment='E=empleados / D=médicos / C=contable / S=superadministrador / A=aduditoria / F=facturación / L=liquidación',
        existing_comment='D=DOCTOR / E=EMPLEADOS DEL COLEGIO / A ADMINISTRADOR')
    op.alter_column('listado_medico', 'EXCEP_HASTA3', existing_type=sa.String(length=6, collation='utf8_spanish2_ci'), type_=mysql.VARCHAR(charset='utf32', collation='utf32_spanish2_ci', length=8), nullable=True)
    op.alter_column('listado_medico', 'EXCEP_DESDE3', existing_type=sa.String(length=6, collation='utf8_spanish2_ci'), type_=mysql.VARCHAR(charset='utf8', collation='utf8_spanish2_ci', length=8), nullable=True)
    op.alter_column('listado_medico', 'EXCEP_HASTA2', existing_type=sa.String(length=6, collation='utf8_spanish2_ci'), type_=mysql.VARCHAR(charset='utf32', collation='utf32_spanish2_ci', length=8), nullable=True)
    op.alter_column('listado_medico', 'EXCEP_DESDE2', existing_type=sa.String(length=6, collation='utf8_spanish2_ci'), type_=mysql.VARCHAR(charset='utf8', collation='utf8_spanish2_ci', length=8), nullable=True)
    op.alter_column('listado_medico', 'EXCEP_HASTA', existing_type=sa.String(length=6, collation='utf8_spanish2_ci'), type_=mysql.VARCHAR(length=8), nullable=True)
    op.alter_column('listado_medico', 'EXCEP_DESDE', existing_type=sa.String(length=6, collation='utf8_spanish2_ci'), type_=mysql.VARCHAR(length=8), nullable=True)
    op.alter_column('listado_medico', 'NRO_ESPECIALIDAD6', existing_type=mysql.INTEGER(display_width=11), nullable=True)
    op.alter_column('listado_medico', 'EXISTE', existing_type=mysql.VARCHAR(length=1), nullable=True)
    op.alter_column('listado_medico', 'CATEGORIA', existing_type=mysql.VARCHAR(length=1), nullable=True)
    op.alter_column('listado_medico', 'OBSERVACION', existing_type=mysql.VARCHAR(length=200), nullable=True)
    op.alter_column('listado_medico', 'VITALICIO', existing_type=mysql.VARCHAR(length=1), nullable=True)
    op.alter_column('listado_medico', 'CODIGO_POSTAL', existing_type=sa.String(length=15, collation='utf8_spanish2_ci'), type_=mysql.INTEGER(display_width=11), nullable=True)
    op.alter_column('listado_medico', 'PROVINCIA', existing_type=sa.String(length=25, collation='utf8_spanish2_ci'), type_=mysql.VARCHAR(length=23), nullable=True)
    op.alter_column('listado_medico', 'COBERTURA', existing_type=mysql.INTEGER(display_width=11), nullable=True)
    op.alter_column('listado_medico', 'FACTURA', existing_type=mysql.VARCHAR(length=2), nullable=True)
    op.alter_column('listado_medico', 'MONOTRIBUTISTA', existing_type=mysql.VARCHAR(length=2), nullable=True)
    op.alter_column('listado_medico', 'MALAPRAXIS', existing_type=sa.String(length=100, collation='utf8_spanish2_ci'), type_=mysql.VARCHAR(length=69), nullable=True)
    op.alter_column('listado_medico', 'ANSSAL', existing_type=mysql.INTEGER(display_width=11), nullable=True)
    op.alter_column('listado_medico', 'CUIT', existing_type=mysql.VARCHAR(length=12), nullable=True)
    op.alter_column('listado_medico', 'DOCUMENTO', existing_type=sa.String(length=8, collation='utf8_spanish2_ci'), type_=mysql.INTEGER(display_width=11), nullable=True)
    op.alter_column('listado_medico', 'TIPO_DOC', existing_type=mysql.VARCHAR(length=3), nullable=True)
    op.alter_column('listado_medico', 'SEXO', existing_type=mysql.VARCHAR(length=1), nullable=True)
    op.alter_column('listado_medico', 'MAIL_PARTICULAR', existing_type=mysql.VARCHAR(length=50), nullable=True)
    op.alter_column('listado_medico', 'CELULAR_PARTICULAR', existing_type=sa.String(length=15, collation='utf8_spanish2_ci'), type_=mysql.VARCHAR(length=14), nullable=True)
    op.alter_column('listado_medico', 'TELE_PARTICULAR', existing_type=sa.String(length=15, collation='utf8_spanish2_ci'), type_=mysql.VARCHAR(length=11), nullable=True)
    op.alter_column('listado_medico', 'DOMICILIO_PARTICULAR', existing_type=sa.String(length=100, collation='utf8_spanish2_ci'), type_=mysql.VARCHAR(length=66), nullable=True)
    op.alter_column('listado_medico', 'MATRICULA_NAC', existing_type=mysql.INTEGER(display_width=11), nullable=True)
    op.alter_column('listado_medico', 'MATRICULA_PROV', existing_type=mysql.INTEGER(display_width=11), nullable=True)
    op.alter_column('listado_medico', 'TELEFONO_CONSULTA', existing_type=mysql.VARCHAR(length=25), nullable=True)
    op.alter_column('listado_medico', 'DOMICILIO_CONSULTA', existing_type=sa.String(length=100, collation='utf8_spanish2_ci'), type_=mysql.VARCHAR(charset='utf8', collation='utf8_spanish_ci', length=103), nullable=True)
    op.alter_column('listado_medico', 'NOMBRE', existing_type=mysql.VARCHAR(length=40), nullable=True)
    op.alter_column('listado_medico', 'NRO_SOCIO', existing_type=mysql.INTEGER(display_width=11), nullable=True)
    op.alter_column('listado_medico', 'NRO_ESPECIALIDAD5', existing_type=mysql.INTEGER(display_width=11), nullable=True)
    op.alter_column('listado_medico', 'NRO_ESPECIALIDAD4', existing_type=mysql.INTEGER(display_width=11), nullable=True)
    op.alter_column('listado_medico', 'NRO_ESPECIALIDAD3', existing_type=mysql.INTEGER(display_width=11), nullable=True)
    op.alter_column('listado_medico', 'NRO_ESPECIALIDAD2', existing_type=mysql.INTEGER(display_width=11), nullable=True)
    op.alter_column('listado_medico', 'NRO_ESPECIALIDAD', existing_type=mysql.INTEGER(display_width=11), nullable=True)
    op.add_column('liquidacion', sa.Column('version', mysql.INTEGER(display_width=11), autoincrement=False, nullable=False))
    op.add_column('liquidacion', sa.Column('nro_liquidacion', mysql.VARCHAR(collation='utf8mb4_unicode_ci', length=30), nullable=True))
    op.add_column('liquidacion', sa.Column('cierre_timestamp', mysql.VARCHAR(collation='utf8mb4_unicode_ci', length=25), nullable=True))
    op.add_column('liquidacion', sa.Column('estado', mysql.ENUM('A', 'C', collation='utf8mb4_unicode_ci'), server_default=sa.text("'A'"), nullable=False))
    op.add_column('liquidacion', sa.Column('resumen_id', mysql.INTEGER(display_width=11), autoincrement=False, nullable=False))
    op.drop_constraint(None, 'liquidacion', type_='foreignkey')
    op.drop_constraint('uq_liq_pago_os_per', 'liquidacion', type_='unique')
    op.drop_index(op.f('ix_liquidacion_pago_id'), table_name='liquidacion')
    op.drop_index('idx_liq_pago_os_per', table_name='liquidacion')
    op.drop_column('liquidacion', 'total_creditos')
    op.drop_column('liquidacion', 'total_gastos')
    op.drop_column('liquidacion', 'total_honorarios')
    op.drop_column('liquidacion', 'nro_factura')
    op.drop_column('liquidacion', 'pago_id')
    op.add_column('detalle_liquidacion', sa.Column('debito_credito_id', mysql.INTEGER(display_width=11), autoincrement=False, nullable=True))
    op.add_column('detalle_liquidacion', sa.Column('importe', mysql.DECIMAL(precision=14, scale=2), nullable=False))
    op.add_column('detalle_liquidacion', sa.Column('prev_detalle_id', mysql.INTEGER(display_width=11), autoincrement=False, nullable=True))
    op.drop_constraint(None, 'detalle_liquidacion', type_='foreignkey')
    op.drop_index(op.f('ix_detalle_liquidacion_prestacion_id'), table_name='detalle_liquidacion')
    op.alter_column('detalle_liquidacion', 'prestacion_id', existing_type=mysql.INTEGER(display_width=11), type_=mysql.VARCHAR(collation='utf8mb4_unicode_ci', length=16), existing_nullable=False)
    op.drop_column('detalle_liquidacion', 'importe_total')
    op.drop_column('detalle_liquidacion', 'gastos')
    op.drop_column('detalle_liquidacion', 'honorarios')
    op.drop_column('descuentos', 'aplica_a_todos')
    op.add_column('deduccion_aplicacion', sa.Column('concepto_tipo', mysql.ENUM('desc', 'esp', collation='utf8mb4_unicode_ci'), nullable=False))
    op.add_column('deduccion_aplicacion', sa.Column('medico_id', mysql.INTEGER(display_width=11), autoincrement=False, nullable=False))
    op.add_column('deduccion_aplicacion', sa.Column('resumen_id', mysql.INTEGER(display_width=11), autoincrement=False, nullable=False))
    op.add_column('deduccion_aplicacion', sa.Column('concepto_id', mysql.INTEGER(display_width=11), autoincrement=False, nullable=False))
    op.drop_constraint(None, 'deduccion_aplicacion', type_='foreignkey')
    op.drop_constraint(None, 'deduccion_aplicacion', type_='foreignkey')
    op.drop_constraint('uq_dedapli_pago_ded', 'deduccion_aplicacion', type_='unique')
    op.drop_index(op.f('ix_deduccion_aplicacion_pago_id'), table_name='deduccion_aplicacion')
    op.drop_index(op.f('ix_deduccion_aplicacion_deduccion_id'), table_name='deduccion_aplicacion')
    op.drop_index('idx_apl_pago', table_name='deduccion_aplicacion')
    op.drop_index('idx_apl_deduccion', table_name='deduccion_aplicacion')
    op.drop_column('deduccion_aplicacion', 'deduccion_id')
    op.drop_column('deduccion_aplicacion', 'pago_id')
    op.drop_index(op.f('ix_recibo_pago_medico_id'), table_name='recibo')
    op.drop_index(op.f('ix_recibo_pago_id'), table_name='recibo')
    op.drop_index(op.f('ix_recibo_nro_recibo'), table_name='recibo')
    op.drop_index(op.f('ix_recibo_medico_id'), table_name='recibo')
    op.drop_index(op.f('ix_recibo_created_by_user'), table_name='recibo')
    op.drop_index('idx_recibo_pago', table_name='recibo')
    op.drop_index('idx_recibo_med', table_name='recibo')
    op.drop_table('recibo')
    op.drop_index(op.f('ix_ajuste_obra_social_id'), table_name='ajuste')
    op.drop_index(op.f('ix_ajuste_medico_id'), table_name='ajuste')
    op.drop_index(op.f('ix_ajuste_lote_id'), table_name='ajuste')
    op.drop_index(op.f('ix_ajuste_id_atencion'), table_name='ajuste')
    op.drop_index(op.f('ix_ajuste_ext_id'), table_name='ajuste')
    op.drop_index(op.f('ix_ajuste_created_by_user'), table_name='ajuste')
    op.drop_index('idx_ajuste_medico', table_name='ajuste')
    op.drop_index('idx_ajuste_lote', table_name='ajuste')
    op.drop_table('ajuste')
    op.drop_index(op.f('ix_socio_descuento_pagador_medico_id'), table_name='socio_descuento')
    op.drop_index(op.f('ix_socio_descuento_medico_id'), table_name='socio_descuento')
    op.drop_index(op.f('ix_socio_descuento_descuento_id'), table_name='socio_descuento')
    op.drop_index(op.f('ix_socio_descuento_created_by_user'), table_name='socio_descuento')
    op.drop_index('idx_med_desc', table_name='socio_descuento')
    op.drop_table('socio_descuento')
    op.drop_index(op.f('ix_pago_medico_pago_id'), table_name='pago_medico')
    op.drop_index(op.f('ix_pago_medico_medico_id'), table_name='pago_medico')
    op.drop_index(op.f('ix_pago_medico_created_by_user'), table_name='pago_medico')
    op.drop_table('pago_medico')
    op.drop_index(op.f('ix_lote_ajuste_snap_origen_id'), table_name='lote_ajuste')
    op.drop_index(op.f('ix_lote_ajuste_pago_id'), table_name='lote_ajuste')
    op.drop_index(op.f('ix_lote_ajuste_obra_social_id'), table_name='lote_ajuste')
    op.drop_index(op.f('ix_lote_ajuste_created_by_user'), table_name='lote_ajuste')
    op.drop_index('idx_lote_pago', table_name='lote_ajuste')
    op.drop_index('idx_lote_os_per', table_name='lote_ajuste')
    op.drop_table('lote_ajuste')
    op.drop_index(op.f('ix_deducciones_pagador_medico_id'), table_name='deducciones')
    op.drop_index(op.f('ix_deducciones_medico_id'), table_name='deducciones')
    op.drop_index(op.f('ix_deducciones_generado_en_pago_id'), table_name='deducciones')
    op.drop_index(op.f('ix_deducciones_descuento_id'), table_name='deducciones')
    op.drop_index(op.f('ix_deducciones_created_by_user'), table_name='deducciones')
    op.drop_index('idx_ded_periodo', table_name='deducciones')
    op.drop_index('idx_ded_origen_estado', table_name='deducciones')
    op.drop_table('deducciones')
    op.drop_index(op.f('ix_pago_created_by_user'), table_name='pago')
    op.drop_table('pago')
