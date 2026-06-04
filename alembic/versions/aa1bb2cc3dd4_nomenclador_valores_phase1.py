"""nomenclador_valores_phase1

Revision ID: aa1bb2cc3dd4
Revises: s2t3u4v5w6x7, f1bae5c76541
Create Date: 2026-05-30

Crea las 10 tablas del sistema de nomenclador y valores (prefijo nm_).
Mergea los dos heads existentes: s2t3u4v5w6x7 y f1bae5c76541.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "aa1bb2cc3dd4"
down_revision: Union[str, Sequence[str], None] = "s2t3u4v5w6x7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── nm_nomenclador ──────────────────────────────────────────────────────
    op.create_table(
        "nm_nomenclador",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("codigo", sa.String(20), nullable=False),
        sa.Column("proviene_de_id", sa.Integer(), nullable=True),
        sa.Column("descripcion", sa.String(255), nullable=False),
        sa.Column("categoria", sa.String(100), nullable=True),
        sa.Column(
            "complejidad",
            sa.Enum("baja", "media", "alta", name="nm_complejidad_enum"),
            nullable=True,
        ),
        sa.Column("sin_restriccion_especialidad", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("observacion", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()"),
                  onupdate=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["proviene_de_id"], ["nm_nomenclador.id"]),
        sa.UniqueConstraint("codigo", name="uq_nm_nomenclador_codigo"),
    )
    op.create_index("ix_nm_nomenclador_codigo", "nm_nomenclador", ["codigo"])
    op.create_index("ix_nm_nomenclador_complejidad", "nm_nomenclador", ["complejidad"])
    op.create_index("ix_nm_nomenclador_proviene_de_id", "nm_nomenclador", ["proviene_de_id"])

    # ── nm_nomenclador_especialidad ──────────────────────────────────────────
    op.create_table(
        "nm_nomenclador_especialidad",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("nomenclador_id", sa.Integer(), nullable=False),
        sa.Column("especialidad_id_colegio", sa.Integer(), nullable=False),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("observacion", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["nomenclador_id"], ["nm_nomenclador.id"]),
        sa.UniqueConstraint("nomenclador_id", "especialidad_id_colegio", name="uq_nm_nom_esp"),
    )
    op.create_index("ix_nm_nom_esp_especialidad", "nm_nomenclador_especialidad", ["especialidad_id_colegio"])

    # ── nm_medico_codigo_habilitado ──────────────────────────────────────────
    op.create_table(
        "nm_medico_codigo_habilitado",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("medico_id", sa.Integer(), nullable=False),
        sa.Column("nomenclador_id", sa.Integer(), nullable=False),
        sa.Column(
            "tipo",
            sa.Enum("habilita", "inhabilita", name="nm_tipo_habilitacion_enum"),
            nullable=False,
        ),
        sa.Column("vigencia_desde", sa.Date(), nullable=True),
        sa.Column("vigencia_hasta", sa.Date(), nullable=True),
        sa.Column("motivo", sa.String(500), nullable=True),
        sa.Column("observacion", sa.Text(), nullable=True),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["medico_id"], ["listado_medico.ID"]),
        sa.ForeignKeyConstraint(["nomenclador_id"], ["nm_nomenclador.id"]),
    )
    op.create_index("ix_nm_mch_medico_nom", "nm_medico_codigo_habilitado", ["medico_id", "nomenclador_id"])
    op.create_index("ix_nm_mch_tipo", "nm_medico_codigo_habilitado", ["tipo"])
    op.create_index("ix_nm_mch_vigencia", "nm_medico_codigo_habilitado", ["vigencia_desde", "vigencia_hasta"])

    # ── nm_homologador ───────────────────────────────────────────────────────
    op.create_table(
        "nm_homologador",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("obra_social_nro", sa.Integer(), nullable=False),
        sa.Column("codigo_origen", sa.String(50), nullable=False),
        sa.Column("nomenclador_id", sa.Integer(), nullable=False),
        sa.Column("descripcion_origen", sa.String(255), nullable=True),
        sa.Column("observacion", sa.Text(), nullable=True),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["nomenclador_id"], ["nm_nomenclador.id"]),
        sa.UniqueConstraint("obra_social_nro", "codigo_origen", name="uq_nm_homologador_os_origen"),
    )
    op.create_index("ix_nm_homologador_os_origen", "nm_homologador", ["obra_social_nro", "codigo_origen"])
    op.create_index("ix_nm_homologador_nomenclador", "nm_homologador", ["nomenclador_id"])

    # ── nm_convenios ─────────────────────────────────────────────────────────
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

    # ── nm_tecnicas ──────────────────────────────────────────────────────────
    op.create_table(
        "nm_tecnicas",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("codigo", sa.String(50), nullable=False),
        sa.Column("nombre", sa.String(100), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=True),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("codigo", name="uq_nm_tecnicas_codigo"),
    )

    # ── nm_galenos ───────────────────────────────────────────────────────────
    op.create_table(
        "nm_galenos",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("obra_social_nro", sa.Integer(), nullable=False),
        sa.Column("convenio_id", sa.Integer(), nullable=False),
        sa.Column("codigo", sa.String(100), nullable=False),
        sa.Column("nombre", sa.String(200), nullable=False),
        sa.Column(
            "tipo",
            sa.Enum("galeno", "gasto", "modulo", "otro", name="nm_tipo_galeno_enum"),
            nullable=False,
        ),
        sa.Column("vigencia_desde", sa.Date(), nullable=False),
        sa.Column("vigencia_hasta", sa.Date(), nullable=True),
        sa.Column("valor_unitario", sa.DECIMAL(14, 2), nullable=False),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("observacion", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["convenio_id"], ["nm_convenios.id"]),
    )
    op.create_index("ix_nm_galenos_os_conv_codigo", "nm_galenos", ["obra_social_nro", "convenio_id", "codigo"])
    op.create_index("ix_nm_galenos_vigencia", "nm_galenos", ["vigencia_desde", "vigencia_hasta"])
    op.create_index("ix_nm_galenos_tipo", "nm_galenos", ["tipo"])

    # ── nm_valores ───────────────────────────────────────────────────────────
    op.create_table(
        "nm_valores",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("obra_social_nro", sa.Integer(), nullable=False),
        sa.Column("convenio_id", sa.Integer(), nullable=False),
        sa.Column("nomenclador_id", sa.Integer(), nullable=False),
        sa.Column("codigo", sa.String(20), nullable=False),
        sa.Column("descripcion", sa.String(255), nullable=True),
        sa.Column("nivel", sa.Integer(), nullable=True),
        sa.Column("vigencia_desde", sa.Date(), nullable=False),
        sa.Column("vigencia_hasta", sa.Date(), nullable=True),
        sa.Column(
            "estado",
            sa.Enum("activo", "cerrado", name="nm_estado_valor_enum"),
            nullable=False,
            server_default="activo",
        ),
        sa.Column("observacion", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["convenio_id"], ["nm_convenios.id"]),
        sa.ForeignKeyConstraint(["nomenclador_id"], ["nm_nomenclador.id"]),
    )
    op.create_index("ix_nm_valores_os_conv_nom", "nm_valores", ["obra_social_nro", "convenio_id", "nomenclador_id"])
    op.create_index("ix_nm_valores_codigo", "nm_valores", ["codigo"])
    op.create_index("ix_nm_valores_vigencia", "nm_valores", ["vigencia_desde", "vigencia_hasta"])
    op.create_index("ix_nm_valores_nivel", "nm_valores", ["nivel"])
    op.create_index("ix_nm_valores_estado", "nm_valores", ["estado"])

    # ── nm_valor_componentes ─────────────────────────────────────────────────
    op.create_table(
        "nm_valor_componentes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("valor_id", sa.Integer(), nullable=False),
        sa.Column(
            "concepto",
            sa.Enum("Honorarios", "Ayudante", "Gastos", name="nm_concepto_componente_enum"),
            nullable=False,
        ),
        sa.Column("galeno_id", sa.Integer(), nullable=True),
        sa.Column("cantidad", sa.DECIMAL(10, 4), nullable=False, server_default="0"),
        sa.Column("valor_unitario", sa.DECIMAL(14, 2), nullable=True),
        sa.Column("opcional", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("orden", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("observacion", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["valor_id"], ["nm_valores.id"]),
        sa.ForeignKeyConstraint(["galeno_id"], ["nm_galenos.id"]),
    )
    op.create_index("ix_nm_vc_valor_id", "nm_valor_componentes", ["valor_id"])
    op.create_index("ix_nm_vc_galeno_id", "nm_valor_componentes", ["galeno_id"])

    # ── nm_historial_precio_codigo ────────────────────────────────────────────
    op.create_table(
        "nm_historial_precio_codigo",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("nomenclador_id", sa.Integer(), nullable=False),
        sa.Column("obra_social_nro", sa.Integer(), nullable=False),
        sa.Column("convenio_id", sa.Integer(), nullable=False),
        sa.Column("vigencia_desde", sa.Date(), nullable=False),
        sa.Column("vigencia_hasta", sa.Date(), nullable=True),
        sa.Column("precio_total", sa.DECIMAL(14, 2), nullable=False),
        sa.Column("valores_id", sa.Integer(), nullable=False),
        sa.Column("componentes_snapshot", sa.JSON(), nullable=False),
        sa.Column(
            "motivo_cambio",
            sa.Enum(
                "carga_inicial",
                "galeno_actualizado",
                "valor_fijo_actualizado",
                "valores_estructura",
                "convenio_nuevo",
                "migracion_legacy",
                name="nm_motivo_cambio_enum",
            ),
            nullable=False,
        ),
        sa.Column("referencia_cambio_id", sa.Integer(), nullable=True),
        sa.Column("fecha_cambio", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["nomenclador_id"], ["nm_nomenclador.id"]),
        sa.ForeignKeyConstraint(["convenio_id"], ["nm_convenios.id"]),
        sa.ForeignKeyConstraint(["valores_id"], ["nm_valores.id"]),
        sa.UniqueConstraint(
            "nomenclador_id", "obra_social_nro", "convenio_id", "vigencia_desde",
            name="uq_nm_historial_precio",
        ),
    )
    op.create_index(
        "ix_nm_historial_nom_os_vigencia",
        "nm_historial_precio_codigo",
        ["nomenclador_id", "obra_social_nro", "vigencia_desde", "vigencia_hasta"],
    )
    op.create_index("ix_nm_historial_os_vigencia", "nm_historial_precio_codigo", ["obra_social_nro", "vigencia_desde"])
    op.create_index("ix_nm_historial_motivo", "nm_historial_precio_codigo", ["motivo_cambio"])
    op.create_index("ix_nm_historial_fecha_cambio", "nm_historial_precio_codigo", ["fecha_cambio"])


def downgrade() -> None:
    op.drop_table("nm_historial_precio_codigo")
    op.drop_table("nm_valor_componentes")
    op.drop_table("nm_valores")
    op.drop_table("nm_galenos")
    op.drop_table("nm_tecnicas")
    op.drop_table("nm_convenios")
    op.drop_table("nm_homologador")
    op.drop_table("nm_medico_codigo_habilitado")
    op.drop_table("nm_nomenclador_especialidad")
    op.drop_table("nm_nomenclador")
    # drop ENUMs
    sa.Enum(name="nm_complejidad_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="nm_tipo_habilitacion_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="nm_estado_convenio_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="nm_tipo_galeno_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="nm_estado_valor_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="nm_concepto_componente_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="nm_motivo_cambio_enum").drop(op.get_bind(), checkfirst=True)
