"""Agregar tablas boletin_observacion y boletin_observacion_plantilla

Revision ID: g2h3i4j5k6l7
Revises: e8f1a2b3c4d5
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa

revision = "g2h3i4j5k6l7"
down_revision = "e8f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "boletin_observacion",
        sa.Column("nro_obrasocial", sa.Integer(), nullable=False),
        sa.Column("texto", sa.String(400), nullable=False, server_default=""),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("nro_obrasocial"),
    )

    op.create_table(
        "boletin_observacion_plantilla",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("texto", sa.String(400), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("texto"),
    )


def downgrade() -> None:
    op.drop_table("boletin_observacion_plantilla")
    op.drop_table("boletin_observacion")
