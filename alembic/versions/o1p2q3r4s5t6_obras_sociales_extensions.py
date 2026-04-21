"""obras_sociales_extensions

Revision ID: o1p2q3r4s5t6
Revises: n4o5p6q7r8s9
Create Date: 2026-04-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = 'o1p2q3r4s5t6'
down_revision: Union[str, Sequence[str], None] = 'n4o5p6q7r8s9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTA: obras_sociales_contactos, obras_sociales_direccion y
    # obras_sociales_documentos ya fueron creadas antes de que fallara
    # una ejecución parcial anterior — se omite su creación.

    # ── Ampliar CUIT (ya existe como varchar(11), lo llevamos a varchar(20)) ──
    op.alter_column('obras_sociales', 'CUIT',
        existing_type=mysql.VARCHAR(length=11),
        type_=sa.String(length=20),
        existing_nullable=True)

    # ── Nuevas columnas en obras_sociales ─────────────────────────────────
    op.add_column('obras_sociales', sa.Column('direccion_real', sa.String(length=200), nullable=True))
    op.add_column('obras_sociales', sa.Column('condicion_iva', sa.Enum('responsable_inscripto', 'exento', name='condicion_iva_enum'), nullable=True))
    op.add_column('obras_sociales', sa.Column('plazo_vencimiento', sa.Integer(), nullable=True))
    op.add_column('obras_sociales', sa.Column('fecha_alta_convenio', sa.Date(), nullable=True))
    op.add_column('obras_sociales', sa.Column('obra_social_principal_id', sa.Integer(), nullable=True))
    op.add_column('obras_sociales', sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False))
    op.add_column('obras_sociales', sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'), nullable=False))
    op.create_index(op.f('ix_obras_sociales_obra_social_principal_id'), 'obras_sociales', ['obra_social_principal_id'], unique=False)
    op.create_foreign_key(None, 'obras_sociales', 'obras_sociales', ['obra_social_principal_id'], ['ID'], ondelete='SET NULL')

    # ── Columnas legacy obsoletas ─────────────────────────────────────────
    op.drop_column('obras_sociales', 'TIPO_FACT')


def downgrade() -> None:
    op.add_column('obras_sociales', sa.Column('CUIT', mysql.VARCHAR(charset='utf8mb4', collation='utf8mb4_spanish2_ci', length=11), server_default=sa.text("'0'"), nullable=True))
    op.add_column('obras_sociales', sa.Column('TIPO_FACT', mysql.VARCHAR(charset='utf8mb4', collation='utf8mb4_spanish2_ci', length=1), server_default=sa.text("'A'"), nullable=True))
    op.drop_constraint(None, 'obras_sociales', type_='foreignkey')
    op.drop_index(op.f('ix_obras_sociales_obra_social_principal_id'), table_name='obras_sociales')
    op.drop_column('obras_sociales', 'updated_at')
    op.drop_column('obras_sociales', 'created_at')
    op.drop_column('obras_sociales', 'obra_social_principal_id')
    op.drop_column('obras_sociales', 'fecha_alta_convenio')
    op.drop_column('obras_sociales', 'plazo_vencimiento')
    op.drop_column('obras_sociales', 'condicion_iva')
    op.drop_column('obras_sociales', 'direccion_real')
    op.drop_column('obras_sociales', 'cuit')
    op.drop_index(op.f('ix_obras_sociales_documentos_obra_social_id'), table_name='obras_sociales_documentos')
    op.drop_table('obras_sociales_documentos')
    op.drop_index(op.f('ix_obras_sociales_direccion_obra_social_id'), table_name='obras_sociales_direccion')
    op.drop_table('obras_sociales_direccion')
    op.drop_index(op.f('ix_obras_sociales_contactos_obra_social_id'), table_name='obras_sociales_contactos')
    op.drop_table('obras_sociales_contactos')
