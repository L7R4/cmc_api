"""drop_proviene_de_id

Revision ID: d8e9f0a1b2c3
Revises: c6d7e8f9a0b1
Create Date: 2026-07-30

Elimina `nm_nomenclador.proviene_de_id` (FK auto-referencial + índice). Modelaba
variantes "por técnica" (tradicional/laparoscópica/robótica) como códigos
derivados — diseño documentado pero nunca usado: las 5175 filas de
`nm_nomenclador` en producción tienen `proviene_de_id = NULL` (verificado antes
de escribir esta migración) y la tabla `nm_tecnicas` que lo acompañaba ya había
sido dropeada en `e9f0a1b2c3d4`.

El criterio definitivo (decisión del Colegio): un solo código por práctica; la
vía queda en `detalle_facturacion.via` (ver migración `c6d7e8f9a0b1`) y el
precio laparoscópico se deriva a demanda en
`app/modules/nomenclador/service_vias.py`.

Pre-check recomendado antes de correr esto en un ambiente con datos propios:
    SELECT COUNT(*) FROM nm_nomenclador WHERE proviene_de_id IS NOT NULL;  -- debe dar 0

Nombre de FK/índice verificados contra la base de datos real
(`information_schema.KEY_COLUMN_USAGE` / `SHOW INDEX`).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, None] = "c6d7e8f9a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("nm_nomenclador_ibfk_1", "nm_nomenclador", type_="foreignkey")
    op.drop_index("ix_nm_nomenclador_proviene_de_id", table_name="nm_nomenclador")
    op.drop_column("nm_nomenclador", "proviene_de_id")


def downgrade() -> None:
    op.add_column(
        "nm_nomenclador",
        sa.Column("proviene_de_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_nm_nomenclador_proviene_de_id", "nm_nomenclador", ["proviene_de_id"],
    )
    op.create_foreign_key(
        "nm_nomenclador_ibfk_1", "nm_nomenclador", "nm_nomenclador",
        ["proviene_de_id"], ["id"],
    )
