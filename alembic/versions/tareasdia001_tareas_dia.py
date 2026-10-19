"""Cuaderno de tareas del Inicio (tabla tareas_dia)

Tabla nueva. **No toca nada existente**: ni columnas, ni permisos, ni roles. Los
endpoints se autorizan con SOLO_AUTENTICADO (la restricción es de propiedad: cada
usuario ve sólo las suyas), así que no hay nada que sincronizar en `permissions`.

El `downgrade` borra la tabla. Es destructivo y no hay forma de que no lo sea: lo
que tenga adentro se cargó después de esta revisión.

Revision ID: tareasdia001
Revises: nomesc01
Create Date: 2026-10-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tareasdia001"
down_revision: Union[str, Sequence[str], None] = "nomesc01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tareas_dia",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "medico_id",
            sa.Integer(),
            sa.ForeignKey("listado_medico.ID", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("texto", sa.String(140), nullable=False),
        sa.Column("hecha", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("prioridad", sa.String(10), nullable=False, server_default="media"),
        sa.Column("hecha_el", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_tareas_dia_medico", "tareas_dia", ["medico_id"])


def downgrade() -> None:
    op.drop_index("ix_tareas_dia_medico", table_name="tareas_dia")
    op.drop_table("tareas_dia")
