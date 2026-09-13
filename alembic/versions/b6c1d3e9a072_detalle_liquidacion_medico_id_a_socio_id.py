"""detalle_liquidacion_medico_id_a_socio_id

Revision ID: b6c1d3e9a072
Revises: f6a7b8c9d0e1
Create Date: 2026-09-12

Unifica detalle_liquidacion.medico_id a listado_medico.ID en toda la tabla.

Contexto: build_detalles_from_cmc() (fuente='cmc') siempre guardó ID, pero el
flujo legacy que generó los registros fuente='ga' guardaba NRO_SOCIO en la
misma columna. El resto del sistema (Ajuste, PagoMedico, Recibo, Deduccion,
SocioDescuento) usa ID como identidad del médico; el código que leía
detalle_liquidacion se corrigió para asumir ID de forma consistente. Esta
migración traduce los registros legacy 'ga' (NRO_SOCIO -> ID) para que sigan
encontrándose con el código corregido, y agrega la FK que faltaba para que
la columna no pueda volver a divergir en silencio.
"""
from alembic import op
import sqlalchemy as sa


revision = 'b6c1d3e9a072'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Traduce medico_id de NRO_SOCIO -> ID solo para los registros legacy 'ga'.
    # Los 'cmc' ya guardan ID desde que build_detalles_from_cmc existe.
    op.execute("""
        UPDATE detalle_liquidacion dl
        JOIN listado_medico lm ON lm.NRO_SOCIO = dl.medico_id
        SET dl.medico_id = lm.ID
        WHERE dl.fuente = 'ga'
    """)

    op.create_foreign_key(
        "fk_detliq_medico",
        "detalle_liquidacion",
        "listado_medico",
        ["medico_id"],
        ["ID"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_detliq_medico", "detalle_liquidacion", type_="foreignkey")

    op.execute("""
        UPDATE detalle_liquidacion dl
        JOIN listado_medico lm ON lm.ID = dl.medico_id
        SET dl.medico_id = lm.NRO_SOCIO
        WHERE dl.fuente = 'ga'
    """)
