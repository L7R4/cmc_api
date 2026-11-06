"""Factura de la refacturación dentro del lote.

Una refacturación genera para el Colegio una factura nueva de AFIP/ARCA. `lote_ajuste` guarda su tipo (A/B/C),
su número (texto libre) y un único archivo (ruta relativa en uploads/lotes/<id>/ y nombre original).
Aditivo y nullable: los lotes existentes quedan en NULL. Idempotente.

Revision ID: lotefact02
Revises: cbudos02
Create Date: 2026-11-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "lotefact02"
down_revision: Union[str, Sequence[str], None] = "cbudos02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDICE = "ix_lote_ajuste_refact_nro"
COLUMNAS = (
    ("refact_tipo_factura", sa.String(1)),
    ("refact_nro_factura", sa.String(20)),
    ("refact_documento_path", sa.String(300)),
    ("refact_documento_nombre", sa.String(255)),
)


def _columnas() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("lote_ajuste")}


def _indices() -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("lote_ajuste")}


def upgrade() -> None:
    existentes = _columnas()
    for nombre, tipo in COLUMNAS:
        if nombre not in existentes:
            op.add_column("lote_ajuste", sa.Column(nombre, tipo, nullable=True))
    if INDICE not in _indices():
        op.create_index(INDICE, "lote_ajuste", ["refact_nro_factura"])


def downgrade() -> None:
    if INDICE in _indices():
        op.drop_index(INDICE, table_name="lote_ajuste")
    existentes = _columnas()
    for nombre, _ in reversed(COLUMNAS):
        if nombre in existentes:
            op.drop_column("lote_ajuste", nombre)
