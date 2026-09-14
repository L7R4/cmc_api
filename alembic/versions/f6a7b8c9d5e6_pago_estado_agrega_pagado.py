"""pago_estado_agrega_pagado

Revision ID: f6a7b8c9d5e6
Revises: e5f6a7b8c9d5
Create Date: 2026-09-14

Agrega 'P' (Pagado) al enum pago.estado — antes solo A/C. Es un estado
terminal que el operador marca a mano una vez efectuado el pago bancario,
sin exigir que los recibos ya estén emitidos (son procesos independientes:
el pago real puede ocurrir antes de que se emitan los recibos). Desde P no
se puede reabrir un pago (ver pagos/routes.py reabrir_pago).

Ligado también a que, desde este mismo cambio, cerrar un pago marca en
detalle_facturacion las prestaciones que liquidó como 'L' (y reabrir las
devuelve a 'C') — ver services/detalle_facturacion_update.py y diagnóstico
C3/A7.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = 'f6a7b8c9d5e6'
down_revision = 'e5f6a7b8c9d5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "pago",
        "estado",
        existing_type=mysql.ENUM("A", "C"),
        type_=mysql.ENUM("A", "C", "P"),
        existing_nullable=False,
        existing_server_default=sa.text("'A'"),
    )


def downgrade() -> None:
    op.alter_column(
        "pago",
        "estado",
        existing_type=mysql.ENUM("A", "C", "P"),
        type_=mysql.ENUM("A", "C"),
        existing_nullable=False,
        existing_server_default=sa.text("'A'"),
    )
