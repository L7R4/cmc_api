"""Avisos de la agenda para los administradores (tabla agenda_avisos)

Tabla nueva. **No toca nada existente**: ni columnas, ni permisos, ni roles. Los
endpoints se autorizan con SOLO_AUTENTICADO y MAQUINA, así que no hay nada que
sincronizar en `permissions`.

El `downgrade` borra la tabla; lo que tenga adentro se generó después de esta
revisión y se regenera solo cada día.

Revision ID: agendaavisos01
Revises: saldodeu01
Create Date: 2026-11-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "agendaavisos01"
down_revision: Union[str, Sequence[str], None] = "saldodeu01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agenda_avisos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "medico_id",
            sa.Integer(),
            sa.ForeignKey("listado_medico.ID", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "evento_id",
            sa.Integer(),
            sa.ForeignKey("agenda_eventos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("leido", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("medico_id", "evento_id", "fecha", name="uq_agenda_avisos"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_agenda_avisos_medico_fecha", "agenda_avisos", ["medico_id", "fecha", "leido"]
    )


def downgrade() -> None:
    op.drop_index("ix_agenda_avisos_medico_fecha", table_name="agenda_avisos")
    op.drop_table("agenda_avisos")
