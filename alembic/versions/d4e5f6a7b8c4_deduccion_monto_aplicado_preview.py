"""deduccion_monto_aplicado_preview

Revision ID: d4e5f6a7b8c4
Revises: c7d8e9f0a1b3
Create Date: 2026-09-12

Separa la vista previa de deducciones (cuánto se cobraría si el pago abierto
cerrara ahora) del ledger real de lo efectivamente cobrado.

Antes, `_recalcular_montos_aplicados_en_pago` reseteaba y reescribía la
preview directamente en `deducciones.monto_aplicado` — la misma columna que
`aplicar_deducciones_al_cierre` usa como base ("cuánto ya se cobró") para
calcular lo que falta cobrar al cerrar. Eso hacía que el cierre sumara el
cobro real ENCIMA de la preview, marcando deducciones como 'aplicado' con
`monto_aplicado == calculado_total` habiendo cobrado en la realidad (via
`deduccion_aplicacion`) mucho menos. Ver diagnóstico C2.

1. Agrega `monto_aplicado_preview` (nueva columna, arranca en 0 — se
   recalcula sola en el próximo refresco de cualquier pago abierto).
2. Repara `monto_aplicado` para TODAS las filas recomputándolo desde
   `deduccion_aplicacion` (la única fuente que el bug nunca tocó): pasa a
   ser exactamente la suma de lo realmente cobrado, sea cual sea el valor
   corrupto que tuviera antes.
"""
from alembic import op
import sqlalchemy as sa


revision = 'd4e5f6a7b8c4'
down_revision = 'c7d8e9f0a1b3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deducciones",
        sa.Column("monto_aplicado_preview", sa.DECIMAL(14, 2), nullable=False, server_default="0.00"),
    )

    op.execute("""
        UPDATE deducciones d
        SET d.monto_aplicado = (
            SELECT COALESCE(SUM(da.aplicado), 0)
            FROM deduccion_aplicacion da
            WHERE da.deduccion_id = d.id
        )
    """)


def downgrade() -> None:
    op.drop_column("deducciones", "monto_aplicado_preview")
