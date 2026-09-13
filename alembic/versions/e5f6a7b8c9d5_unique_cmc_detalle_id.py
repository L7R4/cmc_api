"""unique_cmc_detalle_id

Revision ID: e5f6a7b8c9d5
Revises: d4e5f6a7b8c4
Create Date: 2026-09-12

Convierte el índice de detalle_liquidacion.cmc_detalle_id en UNIQUE. MySQL
permite múltiples NULL en un índice único (todas las filas fuente='ga' lo
tienen NULL), así que esto no afecta el histórico legacy.

Es la garantía a nivel de base de que una misma prestación de
detalle_facturacion (CMC) no puede terminar en dos filas de
detalle_liquidacion — antes solo lo evitaba una revisión de aplicación
acotada a la liquidación que se estaba armando (ver build_detalles_from_cmc
y diagnóstico C3), así que una segunda Liquidacion para la misma OS+período
en otro pago podía volver a traer las mismas prestaciones.
"""
from alembic import op


revision = 'e5f6a7b8c9d5'
down_revision = 'd4e5f6a7b8c4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("idx_detliq_cmc_id", table_name="detalle_liquidacion")
    op.create_index(
        "idx_detliq_cmc_id",
        "detalle_liquidacion",
        ["cmc_detalle_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_detliq_cmc_id", table_name="detalle_liquidacion")
    op.create_index(
        "idx_detliq_cmc_id",
        "detalle_liquidacion",
        ["cmc_detalle_id"],
        unique=False,
    )
