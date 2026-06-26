"""detalle_facturacion_urgencia_nullable

Hace nullable `urgencia` de `detalle_facturacion`: el Colegio dejó de usarla (se quitaron
los inputs via_quirurgica/sub_tipo_nomenclador que la alimentaban). Las filas nuevas la
dejan en NULL. Se conserva la columna (coexistencia con CMC). `nocturno`/`feriado` ya eran
nullable (migración e6f7a8b9c0d1).

Migración a mano con `SET SESSION sql_mode=''` (fechas '0000-00-00' legacy rompen el ALTER
bajo modo estricto MySQL 5.7).

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-06-24 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = 'b9c0d1e2f3a4'
down_revision: Union[str, Sequence[str], None] = 'a8b9c0d1e2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET SESSION sql_mode = ''")
    op.alter_column(
        'detalle_facturacion', 'urgencia',
        existing_type=mysql.CHAR(length=1), nullable=True,
    )


def downgrade() -> None:
    op.execute("SET SESSION sql_mode = ''")
    op.alter_column(
        'detalle_facturacion', 'urgencia',
        existing_type=mysql.CHAR(length=1), nullable=False,
    )
