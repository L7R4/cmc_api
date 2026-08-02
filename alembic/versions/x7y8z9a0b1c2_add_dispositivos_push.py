"""Agregar tabla dispositivos_push

Tokens de dispositivo del app móvil, destinatarios de las notificaciones push de
avisos. El app los registra al iniciar sesión (POST /api/mobile/dispositivos) y
los da de baja al cerrarla. Aditivo: no toca ninguna tabla existente.

La unicidad es por token, no por médico: un socio puede tener varios
dispositivos, y un mismo teléfono puede cambiar de dueño (en ese caso el token
se reasigna en vez de duplicarse, así el dueño anterior deja de recibir avisos).

Revision ID: x7y8z9a0b1c2
Revises: w6x7y8z9a0b1
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "x7y8z9a0b1c2"
down_revision: Union[str, None] = "w6x7y8z9a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dispositivos_push",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("medico_id", sa.Integer(), nullable=False),
        sa.Column("expo_push_token", sa.String(255), nullable=False),
        sa.Column("plataforma", sa.String(20), nullable=True),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "last_seen_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["medico_id"], ["listado_medico.ID"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("expo_push_token", name="uq_dispositivos_push_token"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_dispositivos_push_activo_medico",
        "dispositivos_push",
        ["activo", "medico_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_dispositivos_push_activo_medico", table_name="dispositivos_push")
    op.drop_table("dispositivos_push")
