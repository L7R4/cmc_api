"""drop_fk_ajuste_guardar_atencion

Revision ID: s2t3u4v5w6x7
Revises: r1s2t3u4v5w6
Create Date: 2026-05-03

Elimina la FK ajuste.id_atencion → guardar_atencion.ID.
La columna permanece como INTEGER nullable sin FK.
En el sistema CMC almacena id_detalle_prestaciones de detalle_facturacion.
"""
from alembic import op
import sqlalchemy as sa

revision = 's2t3u4v5w6x7'
down_revision = 'r1s2t3u4v5w6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ajuste_ibfk_2", "ajuste", type_="foreignkey")


def downgrade() -> None:
    op.create_foreign_key(
        "ajuste_ibfk_2",
        "ajuste",
        "guardar_atencion",
        ["id_atencion"],
        ["ID"],
        ondelete="SET NULL",
    )
