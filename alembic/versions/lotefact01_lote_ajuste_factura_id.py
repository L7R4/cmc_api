"""El lote de débitos/créditos cuelga de una factura, no de un período.

Una obra social puede tener, en un mismo período, la factura original y una o más
complementarias (`facturacion.version` 1, 2, 3…), cada una con su número y sus
prestaciones (`detalle_facturacion.version`). Hasta ahora `lote_ajuste` sólo
guardaba obra social + mes + año, así que no se podía saber de cuál factura era
el lote, y el listado lo repetía una vez por factura.

Se agrega `factura_id` = `facturacion.id_prestaciones`. Sin FK, igual que el resto
de las referencias a las tablas de CMC (`facturacion` usa SMALLINT de clave).
Aditivo y nullable: los lotes existentes quedan en NULL y siguen funcionando por
obra social + período; sólo los lotes nuevos nacen atados a una factura.

Revision ID: lotefact01
Revises: diacorte31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "lotefact01"
down_revision: Union[str, None] = "diacorte31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDICE = "ix_lote_ajuste_factura_id"


def _columnas() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("lote_ajuste")}


def upgrade() -> None:
    if "factura_id" not in _columnas():
        op.add_column("lote_ajuste", sa.Column("factura_id", sa.Integer(), nullable=True))
        op.create_index(INDICE, "lote_ajuste", ["factura_id"])


def downgrade() -> None:
    if "factura_id" in _columnas():
        op.drop_index(INDICE, table_name="lote_ajuste")
        op.drop_column("lote_ajuste", "factura_id")
