"""obras_sociales_pagos — registro de deuda de la obra social

Una tabla nueva. No toca nada existente, ni permisos ni roles: los endpoints
usan `catalogo:leer` / `catalogo:editar`, que ya están en el catálogo.

Es un registro deliberadamente chico: **fecha, monto y estado**, más la factura
adjunta. No lleva concepto, período, vencimiento ni monto cobrado — el Colegio
pidió el registro mínimo, y campos que nadie completa sólo ensucian la pantalla.

`estado` es una columna real y no un derivado: sin un "monto cobrado" contra el
cual compararlo, no hay nada de dónde calcularlo. Lo marca quien carga la fila.

Revision ID: ospag0s0001
Revises: inst4g3nd001
Create Date: 2026-08-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ospag0s0001"
down_revision: Union[str, Sequence[str], None] = "inst4g3nd001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "obras_sociales_pagos",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("obra_social_id", sa.Integer(), nullable=False),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("monto", sa.DECIMAL(precision=14, scale=2), nullable=False),
        sa.Column(
            "estado",
            sa.Enum("pendiente", "parcial", "pagado", name="os_pago_estado_enum"),
            server_default="pendiente",
            nullable=False,
        ),
        # Ruta relativa del adjunto, servida por /api/archivos/…. El archivo va
        # a uploads/obras_sociales/<id>/, que ya tiene regla de autorización.
        sa.Column("factura_url", sa.String(length=300), nullable=True),
        sa.Column("factura_nombre", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True,
        ),
        sa.Column("actualizado_por", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["obra_social_id"], ["obras_sociales.ID"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    # Sin `create_index` para `obra_social_id`: MySQL crea uno solo al declarar
    # la foreign key, y agregarlo a mano deja un índice duplicado que además
    # traba el downgrade (no se puede dropear un índice que la FK necesita).


def downgrade() -> None:
    # Sólo la tabla: el índice de la FK se va con ella.
    op.drop_table("obras_sociales_pagos")
