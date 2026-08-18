"""solicitud_cambio_medico: cambios + aplicado_at

Pone al día el esquema con el modelo `SolicitudCambioMedico`, que ya declaraba
estas dos columnas pero nunca tuvo migración. Producción SÍ las tiene (se
agregaron a mano allá), así que esta revisión existe para que dev deje de
divergir — en prod el `.sql` equivalente es un no-op.

Sin ellas, cualquier SELECT sobre la tabla falla con error 1054 y el módulo de
solicitudes de cambio del médico responde 500.

Tipos tomados del esquema real de producción:
    cambios      longtext NULL   (el modelo lo mapea como JSON)
    aplicado_at  datetime NULL

Revision ID: b4c5d6e7f8a1
Revises: a3b4c5d6e7f9
Create Date: 2026-08-11

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = 'b4c5d6e7f8a1'
down_revision = 'a3b4c5d6e7f9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "solicitud_cambio_medico",
        sa.Column("cambios", mysql.LONGTEXT(), nullable=True),
    )
    op.add_column(
        "solicitud_cambio_medico",
        sa.Column("aplicado_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("solicitud_cambio_medico", "aplicado_at")
    op.drop_column("solicitud_cambio_medico", "cambios")
