"""Historial de prestaciones y nombre nuevo de la auditoría general

* `audit_log` pasa a llamarse `audit_log_general` (con sus índices).
* Tabla nueva `audit_log_facturacion`: un evento por cambio y por prestación de
  `detalle_facturacion` (ver `app/modules/facturacion/historial.py`). Arranca vacía:
  lo anterior no se reconstruye en esta revisión.

Producción no usa Alembic: el equivalente es `scripts/prod_2026-10-22/`.

Revision ID: auditfact01
Revises: estadocta01
Create Date: 2026-10-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "auditfact01"
down_revision: Union[str, Sequence[str], None] = "estadocta01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDICES = ("timestamp", "route", "user_id", "nro_socio", "status_code", "user_timestamp", "route_timestamp")


def upgrade() -> None:
    op.rename_table("audit_log", "audit_log_general")
    for sufijo in _INDICES:
        op.execute(
            f"ALTER TABLE audit_log_general RENAME INDEX ix_audit_log_{sufijo} TO ix_audit_log_general_{sufijo}"
        )

    op.create_table(
        "audit_log_facturacion",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("prestacion_id", sa.BigInteger(), nullable=False),
        sa.Column("ocurrido_en", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("estado_antes", sa.String(2), nullable=True),
        sa.Column("estado_despues", sa.String(2), nullable=True),
        sa.Column("actor_tipo", sa.String(12), nullable=False),
        sa.Column("actor_socio", sa.Integer(), nullable=True),
        sa.Column("motivo_codigo", sa.String(30), nullable=True),
        sa.Column("motivo_texto", sa.String(255), nullable=True),
        sa.Column("cambios", sa.JSON(), nullable=True),
        sa.Column("contexto", sa.JSON(), nullable=True),
        sa.Column("request_id", sa.String(32), nullable=True),
        sa.Column("fuente", sa.String(12), server_default="en_vivo", nullable=False),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_audit_log_facturacion_prestacion", "audit_log_facturacion", ["prestacion_id", "ocurrido_en"])
    op.create_index("ix_audit_log_facturacion_tipo", "audit_log_facturacion", ["tipo", "ocurrido_en"])
    op.create_index("ix_audit_log_facturacion_actor", "audit_log_facturacion", ["actor_socio", "ocurrido_en"])
    op.create_index("ix_audit_log_facturacion_request", "audit_log_facturacion", ["request_id"])


def downgrade() -> None:
    op.drop_table("audit_log_facturacion")
    for sufijo in _INDICES:
        op.execute(
            f"ALTER TABLE audit_log_general RENAME INDEX ix_audit_log_general_{sufijo} TO ix_audit_log_{sufijo}"
        )
    op.rename_table("audit_log_general", "audit_log")
