"""add_audit_log

Revision ID: 9f1a2b3c4d5e
Revises: 3430b44135fd
Create Date: 2026-07-22

Crea la tabla `audit_log` que registra las peticiones mutadoras (POST/PUT/
PATCH/DELETE) de /api/* y todo /auth/*, capturadas por
`app.middleware.audit.AuditMiddleware`. Ver docs/api/auditoria.md.

Sin FK a `listado_medico` a propósito: el registro de auditoría debe
sobrevivir aunque el médico se elimine.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "9f1a2b3c4d5e"
down_revision: Union[str, None] = "3430b44135fd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("route", sa.String(255), nullable=True),
        sa.Column("query_params", sa.String(1000), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("nro_socio", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(50), nullable=True),
        sa.Column("status_code", sa.SmallInteger(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("request_body", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.String(500), nullable=True),
        sa.Column("request_id", sa.String(32), nullable=True),
    )

    op.create_index("ix_audit_log_timestamp", "audit_log", ["timestamp"])
    op.create_index("ix_audit_log_route", "audit_log", ["route"])
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])
    op.create_index("ix_audit_log_nro_socio", "audit_log", ["nro_socio"])
    op.create_index("ix_audit_log_status_code", "audit_log", ["status_code"])
    op.create_index("ix_audit_log_user_timestamp", "audit_log", ["user_id", "timestamp"])
    op.create_index("ix_audit_log_route_timestamp", "audit_log", ["route", "timestamp"])


def downgrade() -> None:
    op.drop_table("audit_log")
