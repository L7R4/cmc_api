"""valores_v2_sin_convenios

Revision ID: b7c8d9e0f1a2
Revises: f0a1b2c3d4e5
Create Date: 2026-06-11

Refactor del módulo de valores:
- Elimina la dimensión 'convenio' (tabla nm_convenios y columnas convenio_id
  en nm_galenos, nm_valores y nm_historial_precio_codigo). La trazabilidad
  queda garantizada por las vigencias.
- Agrega nm_galenos.nivel — galenos nivelados: una fila por
  (obra_social_nro, codigo, nivel, vigencia_desde).
- Agrega nm_valores.sin_restriccion_especialidad — override por OS (NULL hereda
  del nomenclador).
- Recompone la unique del historial a (nomenclador_id, obra_social_nro,
  vigencia_desde).
- Amplía el enum motivo_cambio con 'replicacion' y 'reversion'
  ('convenio_nuevo' se conserva en DB por filas históricas, pero ya no se usa).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, Sequence[str], None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _drop_fks_on_column(table: str, column: str) -> None:
    """Encuentra y borra las FKs de una columna (los nombres *_ibfk_N son autogenerados)."""
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT DISTINCT CONSTRAINT_NAME
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :t
              AND COLUMN_NAME = :c
              AND REFERENCED_TABLE_NAME IS NOT NULL
            """
        ),
        {"t": table, "c": column},
    ).fetchall()
    for (name,) in rows:
        op.drop_constraint(name, table, type_="foreignkey")


MOTIVO_ENUM_NUEVO = sa.Enum(
    "carga_inicial",
    "galeno_actualizado",
    "valor_fijo_actualizado",
    "valores_estructura",
    "convenio_nuevo",      # legado: solo lectura de filas históricas
    "replicacion",
    "reversion",
    "migracion_legacy",
    name="nm_motivo_cambio_enum",
)

MOTIVO_ENUM_VIEJO = sa.Enum(
    "carga_inicial",
    "galeno_actualizado",
    "valor_fijo_actualizado",
    "valores_estructura",
    "convenio_nuevo",
    "migracion_legacy",
    name="nm_motivo_cambio_enum",
)


def upgrade() -> None:
    # ── nm_galenos ────────────────────────────────────────────────────────────
    _drop_fks_on_column("nm_galenos", "convenio_id")
    op.drop_index("ix_nm_galenos_os_conv_codigo", table_name="nm_galenos")
    op.drop_column("nm_galenos", "convenio_id")
    op.add_column("nm_galenos", sa.Column("nivel", sa.Integer(), nullable=True))
    op.create_index(
        "ix_nm_galenos_os_codigo_nivel", "nm_galenos",
        ["obra_social_nro", "codigo", "nivel"],
    )

    # ── nm_valores ────────────────────────────────────────────────────────────
    _drop_fks_on_column("nm_valores", "convenio_id")
    op.drop_index("ix_nm_valores_os_conv_nom", table_name="nm_valores")
    op.drop_column("nm_valores", "convenio_id")
    op.add_column(
        "nm_valores",
        sa.Column("sin_restriccion_especialidad", sa.Boolean(), nullable=True),
    )
    op.create_index(
        "ix_nm_valores_os_nom", "nm_valores", ["obra_social_nro", "nomenclador_id"]
    )

    # ── nm_historial_precio_codigo ────────────────────────────────────────────
    _drop_fks_on_column("nm_historial_precio_codigo", "convenio_id")
    op.drop_constraint("uq_nm_historial_precio", "nm_historial_precio_codigo", type_="unique")
    op.drop_column("nm_historial_precio_codigo", "convenio_id")
    op.create_unique_constraint(
        "uq_nm_historial_precio",
        "nm_historial_precio_codigo",
        ["nomenclador_id", "obra_social_nro", "vigencia_desde"],
    )
    op.alter_column(
        "nm_historial_precio_codigo",
        "motivo_cambio",
        type_=MOTIVO_ENUM_NUEVO,
        existing_type=MOTIVO_ENUM_VIEJO,
        existing_nullable=False,
    )

    # ── nm_convenios ──────────────────────────────────────────────────────────
    op.drop_table("nm_convenios")


def downgrade() -> None:
    op.create_table(
        "nm_convenios",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("obra_social_nro", sa.Integer(), nullable=False),
        sa.Column("nombre", sa.String(200), nullable=False),
        sa.Column("fecha_inicio", sa.Date(), nullable=False),
        sa.Column("fecha_fin", sa.Date(), nullable=True),
        sa.Column(
            "estado",
            sa.Enum("activo", "cerrado", name="nm_estado_convenio_enum"),
            nullable=False,
            server_default="activo",
        ),
        sa.Column("observacion", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_nm_convenios_os_estado", "nm_convenios", ["obra_social_nro", "estado"])

    op.alter_column(
        "nm_historial_precio_codigo",
        "motivo_cambio",
        type_=MOTIVO_ENUM_VIEJO,
        existing_type=MOTIVO_ENUM_NUEVO,
        existing_nullable=False,
    )
    op.drop_constraint("uq_nm_historial_precio", "nm_historial_precio_codigo", type_="unique")
    # NOTA: el downgrade deja convenio_id NULL; no hay forma de reconstruir el dato
    op.add_column(
        "nm_historial_precio_codigo", sa.Column("convenio_id", sa.Integer(), nullable=True)
    )
    op.create_unique_constraint(
        "uq_nm_historial_precio",
        "nm_historial_precio_codigo",
        ["nomenclador_id", "obra_social_nro", "convenio_id", "vigencia_desde"],
    )

    op.create_index(
        "ix_nm_valores_os_conv_nom_placeholder", "nm_valores",
        ["obra_social_nro", "nomenclador_id"],
    )
    op.drop_index("ix_nm_valores_os_nom", table_name="nm_valores")
    op.drop_column("nm_valores", "sin_restriccion_especialidad")
    op.add_column("nm_valores", sa.Column("convenio_id", sa.Integer(), nullable=True))

    op.drop_index("ix_nm_galenos_os_codigo_nivel", table_name="nm_galenos")
    op.drop_column("nm_galenos", "nivel")
    op.add_column("nm_galenos", sa.Column("convenio_id", sa.Integer(), nullable=True))
    op.create_index(
        "ix_nm_galenos_os_conv_codigo", "nm_galenos",
        ["obra_social_nro", "convenio_id", "codigo"],
    )
