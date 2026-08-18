"""Agregar tabla nm_valores_documentos

El respaldo documental de cada actualización de valores de una obra social: la
nota, el Excel o el CSV con el que llegaron los precios que se cargaron en
`nm_valores`. Se agrupan por `(obra_social_nro, vigencia_desde)`, la misma clave
por la que la pantalla de Historial de Valores agrupa la grilla.

Aditivo: no toca ninguna tabla existente.

Revision ID: nmv4ld0cs001
Revises: s3s10n3sr3v0
Create Date: 2026-08-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "nmv4ld0cs001"
down_revision: Union[str, None] = "s3s10n3sr3v0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "nm_valores_documentos",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("obra_social_nro", sa.Integer(), nullable=False),
        sa.Column("vigencia_desde", sa.Date(), nullable=False),
        sa.Column("nombre_original", sa.String(255), nullable=False),
        sa.Column("path", sa.String(300), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("descripcion", sa.String(255), nullable=True),
        sa.Column("subido_por", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index(
        "ix_nm_valores_doc_os_vigencia",
        "nm_valores_documentos",
        ["obra_social_nro", "vigencia_desde"],
    )


def downgrade() -> None:
    op.drop_index("ix_nm_valores_doc_os_vigencia", table_name="nm_valores_documentos")
    op.drop_table("nm_valores_documentos")
