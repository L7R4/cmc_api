"""detalle_facturacion_cod_clinica_bigint

Amplía `detalle_facturacion.cod_clinica` de `smallint(4)` a `bigint`.

La columna pasó de guardar el id legacy de clínica de CMC (chico) a guardar el
**NRO_SOCIO de la clínica** de `listado_medico` (`es_organizacion=1`), que llega hasta
76910 — fuera del rango de smallint (32767). 848 de las 1601 organizaciones
desbordaban. Se alinea con `cod_med`, que ya es `bigint(10)`.

Migración a mano con `SET SESSION sql_mode=''` (fechas '0000-00-00' legacy).

Revision ID: cf3a91d0e7b2
Revises: d8e9f0a1b2c3
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "cf3a91d0e7b2"
down_revision: Union[str, Sequence[str], None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET SESSION sql_mode = ''")
    op.alter_column(
        "detalle_facturacion", "cod_clinica",
        existing_type=mysql.SMALLINT(display_width=4),
        type_=mysql.BIGINT(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.execute("SET SESSION sql_mode = ''")
    # Truncante para los NRO_SOCIO > 32767 cargados con el modelo nuevo.
    op.alter_column(
        "detalle_facturacion", "cod_clinica",
        existing_type=mysql.BIGINT(),
        type_=mysql.SMALLINT(display_width=4),
        existing_nullable=True,
    )
