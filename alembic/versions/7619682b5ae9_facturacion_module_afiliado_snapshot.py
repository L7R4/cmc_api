"""facturacion_module_afiliado_snapshot

Crea el padrón `afiliado` y agrega la columna `calculo_snapshot` (JSON) a
`detalle_facturacion` para el módulo de facturación.

Migración ESCRITA A MANO de forma aditiva. El autogenerate de Alembic produjo
decenas de diffs espurios/destructivos sobre tablas legacy (drift entre los
modelos ORM y la DB real); se descartaron por completo. Aquí solo van los dos
cambios reales del módulo.

Revision ID: 7619682b5ae9
Revises: e0f1a2b3c4d5
Create Date: 2026-06-15 15:54:00.546819

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '7619682b5ae9'
down_revision: Union[str, Sequence[str], None] = 'e0f1a2b3c4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (aditivo)."""
    # `detalle_facturacion` tiene filas legacy con `created='0000-00-00'`. Agregar
    # una columna fuerza a MySQL 5.7 a copiar la tabla y, bajo el sql_mode estricto
    # por defecto, eso falla al validar esas fechas-cero. Relajamos el modo solo
    # para esta conexión de migración.
    op.execute("SET SESSION sql_mode = ''")

    op.create_table(
        'afiliado',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('dni', sa.String(length=15), nullable=False),
        sa.Column('nombre', sa.String(length=100), nullable=False),
        sa.Column('usuario', sa.String(length=30), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(),
            server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_afiliado_dni'), 'afiliado', ['dni'], unique=True
    )

    op.add_column(
        'detalle_facturacion',
        sa.Column('calculo_snapshot', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('detalle_facturacion', 'calculo_snapshot')
    op.drop_index(op.f('ix_afiliado_dni'), table_name='afiliado')
    op.drop_table('afiliado')
