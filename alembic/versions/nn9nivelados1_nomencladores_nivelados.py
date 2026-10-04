"""nm_nomenclador_nivelado + nm_nomenclador_nivelado_codigo

Nomencladores nivelados compartidos por las obras sociales (Cirugía adulto 7 y 10
niveles, Cirugía infantil 7, FASGO 13, Urología 7…): código → nivel (o N unidades
fijas). Los datos se cargan con `scripts/seed_nomencladores_nivelados.py`.

Solo agrega tablas: el código de `main` sigue funcionando contra una base migrada.

Revision ID: nn9nivelados1
Revises: nn8codos1
Create Date: 2026-10-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "nn9nivelados1"
down_revision: Union[str, Sequence[str], None] = "nn8codos1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "nm_nomenclador_nivelado",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("slug", sa.String(60), nullable=False, unique=True),
        sa.Column("nombre", sa.String(120), nullable=False),
        sa.Column("galeno_grupo", sa.String(100), nullable=False),
        sa.Column("niveles", sa.Integer(), nullable=False),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "nm_nomenclador_nivelado_codigo",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("nomenclador_nivelado_id", sa.Integer(),
                  sa.ForeignKey("nm_nomenclador_nivelado.id"), nullable=False),
        sa.Column("nomenclador_id", sa.Integer(), sa.ForeignKey("nm_nomenclador.id"), nullable=False),
        sa.Column("nivel", sa.Integer(), nullable=True),
        sa.Column("unidades", sa.DECIMAL(10, 2), nullable=True),
        sa.Column("observacion", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("nomenclador_nivelado_id", "nomenclador_id", name="uq_nm_nivelado_codigo"),
    )
    op.create_index(
        "ix_nm_nivelado_codigo_nomenclador", "nm_nomenclador_nivelado_codigo", ["nomenclador_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_nm_nivelado_codigo_nomenclador", table_name="nm_nomenclador_nivelado_codigo")
    op.drop_table("nm_nomenclador_nivelado_codigo")
    op.drop_table("nm_nomenclador_nivelado")
