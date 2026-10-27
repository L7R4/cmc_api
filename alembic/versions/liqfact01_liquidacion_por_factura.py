"""La liquidación de un pago es de una factura, no de una obra social y período.

Una obra social puede tener en un mismo período la factura original y una o más
complementarias (`facturacion.version` 1, 2, 3…). Con la unicidad vieja
(pago + obra social + mes + año) no se podían pagar en la misma corrida, el
selector no mostraba el número de factura y al agregar un período entraban juntas
todas las prestaciones cerradas de todas las facturas.

Se agrega `factura_id` (= `facturacion.id_prestaciones`, sin FK como el resto de
las referencias a CMC) y `factura_version`, y la unicidad pasa a ser pago + factura.
Aditivo: las liquidaciones existentes quedan con `factura_id` NULL. En producción
todavía no hay liquidaciones cargadas.

Revision ID: liqfact01
Revises: auditfact01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "liqfact01"
down_revision: Union[str, None] = "auditfact01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLA = "liquidacion"
UQ_VIEJA = "uq_liq_pago_os_per"
UQ_NUEVA = "uq_liq_pago_factura"
IX_FACTURA = "ix_liquidacion_factura_id"


def _insp():
    return sa.inspect(op.get_bind())


def _columnas() -> set[str]:
    return {c["name"] for c in _insp().get_columns(TABLA)}


def _indices() -> set[str]:
    return {i["name"] for i in _insp().get_indexes(TABLA)}


def upgrade() -> None:
    columnas = _columnas()
    if "factura_id" not in columnas:
        op.add_column(TABLA, sa.Column("factura_id", sa.Integer(), nullable=True))
    if "factura_version" not in columnas:
        op.add_column(TABLA, sa.Column("factura_version", sa.Integer(), nullable=True))
    if IX_FACTURA not in _indices():
        op.create_index(IX_FACTURA, TABLA, ["factura_id"])
    # En MySQL una UNIQUE constraint es un índice: aparece en get_indexes.
    if UQ_VIEJA in _indices():
        op.drop_constraint(UQ_VIEJA, TABLA, type_="unique")
    if UQ_NUEVA not in _indices():
        op.create_unique_constraint(UQ_NUEVA, TABLA, ["pago_id", "factura_id"])


def downgrade() -> None:
    if UQ_NUEVA in _indices():
        op.drop_constraint(UQ_NUEVA, TABLA, type_="unique")
    if UQ_VIEJA not in _indices():
        op.create_unique_constraint(
            UQ_VIEJA, TABLA, ["pago_id", "obra_social_id", "mes_periodo", "anio_periodo"]
        )
    if IX_FACTURA in _indices():
        op.drop_index(IX_FACTURA, table_name=TABLA)
    columnas = _columnas()
    if "factura_version" in columnas:
        op.drop_column(TABLA, "factura_version")
    if "factura_id" in columnas:
        op.drop_column(TABLA, "factura_id")
