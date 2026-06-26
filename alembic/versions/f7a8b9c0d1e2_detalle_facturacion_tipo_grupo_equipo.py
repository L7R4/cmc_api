"""detalle_facturacion_tipo_grupo_equipo

Agrega a `detalle_facturacion` dos columnas nuevas (aditivo):
- `tipo` VARCHAR(30) — categorización única (Consulta|Practica|Honorarios individuales|
  Sanatorio) que reemplaza el uso en API de tpo_serv/tipo_orden (se conservan en DB).
- `grupo_equipo_id` BIGINT — vínculo ayudante/gastos → fila del médico (cabeza del equipo);
  NULL si factura solo. FK lógica self → id_detalle_prestaciones (sin FK física, tabla legacy).

Las columnas viejas (tpo_serv, tipo_orden, tpo_funcion) se conservan (coexistencia).
Migración a mano con `SET SESSION sql_mode=''` (fechas '0000-00-00' legacy rompen el
ALTER bajo modo estricto MySQL 5.7).

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-06-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, Sequence[str], None] = 'e6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (aditivo)."""
    op.execute("SET SESSION sql_mode = ''")
    op.add_column('detalle_facturacion', sa.Column('tipo', sa.String(length=30), nullable=True))
    op.add_column('detalle_facturacion', sa.Column('grupo_equipo_id', mysql.BIGINT(), nullable=True))
    op.create_index('ix_detalle_facturacion_grupo_equipo', 'detalle_facturacion', ['grupo_equipo_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET SESSION sql_mode = ''")
    op.drop_index('ix_detalle_facturacion_grupo_equipo', table_name='detalle_facturacion')
    op.drop_column('detalle_facturacion', 'grupo_equipo_id')
    op.drop_column('detalle_facturacion', 'tipo')
