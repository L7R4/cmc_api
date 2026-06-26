"""detalle_facturacion_tpo_serv_orden_nullable

Hace nullable `tpo_serv` y `tipo_orden` de `detalle_facturacion`: la API/servicios del
Colegio dejaron de poblarlos (reemplazados por `tipo`), así que las filas nuevas los
dejan en NULL. Se conservan las columnas (coexistencia con CMC). `tpo_funcion` se sigue
poblando derivado → queda NOT NULL.

Migración a mano con `SET SESSION sql_mode=''` (fechas '0000-00-00' legacy).

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-06-24 00:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = 'a8b9c0d1e2f3'
down_revision: Union[str, Sequence[str], None] = 'f7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = [("tpo_serv", mysql.CHAR(length=1)), ("tipo_orden", mysql.CHAR(length=1))]


def upgrade() -> None:
    op.execute("SET SESSION sql_mode = ''")
    for col, tipo in _COLS:
        op.alter_column("detalle_facturacion", col, existing_type=tipo, nullable=True)


def downgrade() -> None:
    op.execute("SET SESSION sql_mode = ''")
    for col, tipo in _COLS:
        op.alter_column("detalle_facturacion", col, existing_type=tipo, nullable=False)
