"""nomencladores con escala propia, galeno asociado y modo de precio del galeno

- nm_nomenclador_nivelado: tipo, galeno_codigo, galeno_gastos_codigo
- nm_nomenclador_nivel (nueva): escala de unidades por nivel de cada nomenclador
- nm_galenos.precio_por_nivel: 0 = calculado (unidades x valor unitario), 1 = precio fijo por nivel
- nm_valores.nomenclador_nivelado_id: nomenclador con el que se factura el valor

Mismo DDL que `scripts/nomenclador_escala_2026-10-13.sql` (producción no trackea alembic_version,
ahí se aplica a mano; el script además siembra la escala y el nomenclador 'nacional').

Revision ID: nomesc01
Revises: nivesp01
Create Date: 2026-10-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "nomesc01"
down_revision: Union[str, Sequence[str], None] = "nivesp01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("nm_nomenclador_nivelado", sa.Column("tipo", sa.String(20), nullable=False, server_default="nivelado"))
    op.add_column("nm_nomenclador_nivelado", sa.Column("galeno_codigo", sa.String(100), nullable=True))
    op.add_column(
        "nm_nomenclador_nivelado",
        sa.Column("galeno_gastos_codigo", sa.String(100), nullable=True, server_default="gasto_quirurgico"),
    )
    op.create_table(
        "nm_nomenclador_nivel",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("nomenclador_nivelado_id", sa.Integer(), sa.ForeignKey("nm_nomenclador_nivelado.id"), nullable=False),
        sa.Column("nivel", sa.Integer(), nullable=False),
        sa.Column("unidades_honorarios", sa.DECIMAL(10, 2), nullable=False),
        sa.Column("unidades_ayudante", sa.DECIMAL(10, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("nomenclador_nivelado_id", "nivel", name="uq_nm_nomenclador_nivel"),
    )
    op.add_column("nm_galenos", sa.Column("precio_por_nivel", sa.Boolean(), nullable=False, server_default="0"))
    op.add_column("nm_valores", sa.Column("nomenclador_nivelado_id", sa.Integer(), nullable=True))
    op.create_index("ix_nm_valores_nomenclador_nivelado", "nm_valores", ["nomenclador_nivelado_id"])


def downgrade() -> None:
    op.drop_index("ix_nm_valores_nomenclador_nivelado", table_name="nm_valores")
    op.drop_column("nm_valores", "nomenclador_nivelado_id")
    op.drop_column("nm_galenos", "precio_por_nivel")
    op.drop_table("nm_nomenclador_nivel")
    op.drop_column("nm_nomenclador_nivelado", "galeno_gastos_codigo")
    op.drop_column("nm_nomenclador_nivelado", "galeno_codigo")
    op.drop_column("nm_nomenclador_nivelado", "tipo")
