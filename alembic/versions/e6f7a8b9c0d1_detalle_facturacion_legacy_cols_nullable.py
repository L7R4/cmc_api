"""detalle_facturacion_legacy_cols_nullable

Hace nullable las columnas legacy de `detalle_facturacion` que el Colegio NO completa
al cargar prestaciones (el modelo las mapea Optional y las inserta NULL, pero eran
NOT NULL en la DB legacy → IntegrityError). Se conservan las columnas por compatibilidad
con CMC, solo permitiendo NULL. Las columnas que el flujo del Colegio SIEMPRE setea
(periodo, cod_med, cod_obr, cod_nom, tpo_funcion, sesion, cantidad, montos, manual,
tpo_serv, fecha_practica, tipo_orden, porc, urgencia, estado, usuario) quedan NOT NULL.

Migración ESCRITA A MANO (tabla co-propiedad de CMC). `SET SESSION sql_mode = ''` por
las fechas legacy '0000-00-00' que rompen el ALTER bajo modo estricto de MySQL 5.7.

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-06-23 00:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = 'e6f7a8b9c0d1'
down_revision: Union[str, Sequence[str], None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (columna, tipo existente) — campos legacy que el Colegio no completa
_COLS = [
    ("dni_p",          mysql.VARCHAR(length=20)),
    ("nom_ape_p",      mysql.VARCHAR(length=60)),
    ("cod_clinica",    mysql.SMALLINT(display_width=4)),
    ("id_especialidad", mysql.SMALLINT(display_width=4)),
    ("cod_med_indica", mysql.VARCHAR(length=12)),
    ("codigo_oms",     mysql.VARCHAR(length=12)),
    ("diag",           mysql.VARCHAR(length=100)),
    ("nro_vias",       mysql.VARCHAR(length=2)),
    ("fin_semana",     mysql.VARCHAR(length=6)),
    ("nocturno",       mysql.VARCHAR(length=2)),
    ("feriado",        mysql.CHAR(length=2)),
]


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET SESSION sql_mode = ''")
    for col, tipo in _COLS:
        op.alter_column("detalle_facturacion", col, existing_type=tipo, nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET SESSION sql_mode = ''")
    for col, tipo in _COLS:
        op.alter_column("detalle_facturacion", col, existing_type=tipo, nullable=False)
