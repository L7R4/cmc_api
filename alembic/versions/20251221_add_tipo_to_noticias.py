from alembic import op
import sqlalchemy as sa

# Reemplazá por tu id/fecha
revision = "20251221_add_tipo_to_noticias"
down_revision = "cac9bf3aab59"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column(
        'noticias',
        sa.Column('tipo', sa.String(length=10, collation='utf8_spanish2_ci'), nullable=False, server_default='Noticia')
    )
    # Si tu motor es MySQL, conviene soltar el default en el schema si no lo querés fijo en el DDL:
    # op.alter_column('noticias', 'TIPO', server_default=None)

def downgrade():
    op.drop_column('noticias', 'tipo')
