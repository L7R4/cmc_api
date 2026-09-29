"""fase1 reestructura nomenclador: NN, valor_especialidad, descripcion legacy

Escrita a mano (no autogenerate): el modelo tiene mucho drift preexistente
frente a la DB real en tablas no relacionadas con este cambio (columnas
legacy tipo `proviene_de_id`, comentarios de columna, etc.) que un
autogenerate arrastraba entero. Esta migración toca solo lo de la fase 1 del
plan de reestructura del nomenclador — puramente aditiva, no borra ni
modifica ninguna columna existente.

Revision ID: nn1valesp001
Revises: f243a7554fb4
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "nn1valesp001"
down_revision: Union[str, Sequence[str], None] = "f243a7554fb4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "nm_nomenclador_nacional",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("codigo", sa.String(length=20), nullable=False),
        sa.Column("descripcion", sa.String(length=255), nullable=True),
        sa.Column("unidades_honorarios", sa.DECIMAL(precision=10, scale=2), nullable=True),
        sa.Column("unidades_ayudante", sa.DECIMAL(precision=10, scale=2), nullable=True),
        sa.Column("unidades_gastos", sa.DECIMAL(precision=10, scale=2), nullable=True),
        sa.Column("categoria", sa.String(length=100), nullable=True),
        sa.Column(
            "complejidad",
            sa.Enum("baja", "media", "alta", name="nm_complejidad_nacional_enum"),
            nullable=True,
        ),
        sa.Column("activo", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            onupdate=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("codigo"),
    )
    op.create_index(
        "ix_nm_nomenclador_nacional_codigo", "nm_nomenclador_nacional", ["codigo"],
    )
    op.create_index(
        "ix_nm_nomenclador_nacional_activo", "nm_nomenclador_nacional", ["activo"],
    )

    op.create_table(
        "nm_valor_especialidad",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("obra_social_nro", sa.Integer(), nullable=False),
        sa.Column("codigo", sa.String(length=20), nullable=False),
        sa.Column("especialidad_id_colegio", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            onupdate=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "obra_social_nro", "codigo", "especialidad_id_colegio",
            name="uq_nm_valor_especialidad",
        ),
    )
    op.create_index(
        "ix_nm_valor_especialidad_os_codigo", "nm_valor_especialidad",
        ["obra_social_nro", "codigo"],
    )
    op.create_index(
        "ix_nm_valor_especialidad_especialidad", "nm_valor_especialidad",
        ["especialidad_id_colegio"],
    )

    op.create_table(
        "nm_nomenclador_descripcion_legacy",
        sa.Column("nomenclador_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("obra_social_nro", sa.Integer(), nullable=True),
        sa.Column("codigo", sa.String(length=20), nullable=False),
        sa.Column("descripcion", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("nomenclador_id"),
    )
    op.create_index(
        "ix_nm_nom_desc_legacy_codigo", "nm_nomenclador_descripcion_legacy", ["codigo"],
    )

    op.add_column(
        "nm_nomenclador",
        sa.Column("nomenclador_nacional_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_nm_nomenclador_nn", "nm_nomenclador", ["nomenclador_nacional_id"],
    )
    op.create_foreign_key(
        "fk_nm_nomenclador_nn",
        "nm_nomenclador", "nm_nomenclador_nacional",
        ["nomenclador_nacional_id"], ["id"],
    )

    op.add_column(
        "nm_valores",
        sa.Column(
            "sin_restriccion_especialidad", sa.Boolean(),
            server_default="0", nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("nm_valores", "sin_restriccion_especialidad")

    op.drop_constraint("fk_nm_nomenclador_nn", "nm_nomenclador", type_="foreignkey")
    op.drop_index("ix_nm_nomenclador_nn", table_name="nm_nomenclador")
    op.drop_column("nm_nomenclador", "nomenclador_nacional_id")

    op.drop_index("ix_nm_nom_desc_legacy_codigo", table_name="nm_nomenclador_descripcion_legacy")
    op.drop_table("nm_nomenclador_descripcion_legacy")

    op.drop_index("ix_nm_valor_especialidad_especialidad", table_name="nm_valor_especialidad")
    op.drop_index("ix_nm_valor_especialidad_os_codigo", table_name="nm_valor_especialidad")
    op.drop_table("nm_valor_especialidad")

    op.drop_index("ix_nm_nomenclador_nacional_activo", table_name="nm_nomenclador_nacional")
    op.drop_index("ix_nm_nomenclador_nacional_codigo", table_name="nm_nomenclador_nacional")
    op.drop_table("nm_nomenclador_nacional")
