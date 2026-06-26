"""detalle_facturacion_categoria_nullable

Hace nullable la columna `detalle_facturacion.categoria` (char(1)). El Colegio ya no
usa `categoria` al cargar prestaciones (el modelo la mapea Optional y la inserta NULL),
pero la columna era NOT NULL en la DB legacy → IntegrityError. Se conserva la columna
por compatibilidad con CMC, solo permitiendo NULL.

Migración ESCRITA A MANO (la tabla `detalle_facturacion` es co-propiedad de CMC).
Incluye `SET SESSION sql_mode = ''` porque la tabla tiene fechas legacy '0000-00-00'
que rompen el ALTER bajo el modo estricto de MySQL 5.7 al copiar la tabla.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-06-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, Sequence[str], None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET SESSION sql_mode = ''")
    op.alter_column(
        'detalle_facturacion', 'categoria',
        existing_type=mysql.CHAR(length=1),
        nullable=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET SESSION sql_mode = ''")
    op.alter_column(
        'detalle_facturacion', 'categoria',
        existing_type=mysql.CHAR(length=1),
        nullable=False,
    )
