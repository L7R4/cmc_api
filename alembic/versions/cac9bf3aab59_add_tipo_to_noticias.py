"""add tipo to noticias

Revision ID: cac9bf3aab59
Revises: 98a69a12ff05
Create Date: 2025-12-21 20:53:16.749559

"""
from alembic import op
import sqlalchemy as sa

# Reemplazá por tu id/fecha
revision = "cac9bf3aab59"
down_revision = "98a69a12ff05"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column(
        'noticias',
        sa.Column('TIPO', sa.String(length=10, collation='utf8_spanish2_ci'), nullable=False, server_default='Noticia')
    )
    # Si tu motor es MySQL, conviene soltar el default en el schema si no lo querés fijo en el DDL:
    # op.alter_column('noticias', 'TIPO', server_default=None)

def downgrade():
    op.drop_column('noticias', 'TIPO')
