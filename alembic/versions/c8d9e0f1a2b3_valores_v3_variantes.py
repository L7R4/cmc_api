"""valores_v3_variantes

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-06-11

v3 del módulo de valores:
- Reinicio de datos pactados (decisión F): vacía nm_historial_precio_codigo,
  nm_valor_componentes, nm_valores y nm_galenos. El catálogo (nm_nomenclador),
  el homologador y las habilitaciones se conservan.
- Variantes de precio por especialidad: nm_valores.especialidad_id_colegio
  (NULL = variante base). Reemplaza al override binario
  sin_restriccion_especialidad (se elimina).
- Unicidad real de galenos (decisión C): columna generada nivel_key =
  COALESCE(nivel, -1) + UNIQUE (obra_social_nro, codigo, nivel_key, vigencia_desde).
- Historial por variante: especialidad_id_colegio + columna generada
  especialidad_key; unique (nomenclador_id, obra_social_nro, especialidad_key,
  vigencia_desde).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, Sequence[str], None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Reinicio de datos pactados (orden FK-safe) ───────────────────────────
    op.execute("DELETE FROM nm_historial_precio_codigo")
    op.execute("DELETE FROM nm_valor_componentes")
    op.execute("DELETE FROM nm_valores")
    op.execute("DELETE FROM nm_galenos")
    op.execute("ALTER TABLE nm_historial_precio_codigo AUTO_INCREMENT = 1")
    op.execute("ALTER TABLE nm_valor_componentes AUTO_INCREMENT = 1")
    op.execute("ALTER TABLE nm_valores AUTO_INCREMENT = 1")
    op.execute("ALTER TABLE nm_galenos AUTO_INCREMENT = 1")

    # ── nm_galenos: unicidad real por (OS, codigo, nivel, vigencia) ──────────
    op.add_column(
        "nm_galenos",
        sa.Column(
            "nivel_key",
            sa.Integer(),
            sa.Computed("coalesce(nivel, -1)", persisted=True),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_nm_galenos_os_codigo_nivel_vig",
        "nm_galenos",
        ["obra_social_nro", "codigo", "nivel_key", "vigencia_desde"],
    )

    # ── nm_valores: variantes por especialidad ───────────────────────────────
    op.drop_column("nm_valores", "sin_restriccion_especialidad")
    op.add_column(
        "nm_valores",
        sa.Column("especialidad_id_colegio", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_nm_valores_especialidad", "nm_valores", ["especialidad_id_colegio"]
    )

    # ── nm_historial_precio_codigo: historial por variante ───────────────────
    op.add_column(
        "nm_historial_precio_codigo",
        sa.Column("especialidad_id_colegio", sa.Integer(), nullable=True),
    )
    op.add_column(
        "nm_historial_precio_codigo",
        sa.Column(
            "especialidad_key",
            sa.Integer(),
            sa.Computed("coalesce(especialidad_id_colegio, -1)", persisted=True),
            nullable=False,
        ),
    )
    op.drop_constraint("uq_nm_historial_precio", "nm_historial_precio_codigo", type_="unique")
    op.create_unique_constraint(
        "uq_nm_historial_precio",
        "nm_historial_precio_codigo",
        ["nomenclador_id", "obra_social_nro", "especialidad_key", "vigencia_desde"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_nm_historial_precio", "nm_historial_precio_codigo", type_="unique")
    op.drop_column("nm_historial_precio_codigo", "especialidad_key")
    op.drop_column("nm_historial_precio_codigo", "especialidad_id_colegio")
    op.create_unique_constraint(
        "uq_nm_historial_precio",
        "nm_historial_precio_codigo",
        ["nomenclador_id", "obra_social_nro", "vigencia_desde"],
    )

    op.drop_index("ix_nm_valores_especialidad", table_name="nm_valores")
    op.drop_column("nm_valores", "especialidad_id_colegio")
    op.add_column(
        "nm_valores",
        sa.Column("sin_restriccion_especialidad", sa.Boolean(), nullable=True),
    )

    op.drop_constraint("uq_nm_galenos_os_codigo_nivel_vig", "nm_galenos", type_="unique")
    op.drop_column("nm_galenos", "nivel_key")
    # NOTA: los datos vaciados en upgrade() no se pueden restaurar
