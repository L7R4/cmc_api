"""fase3 reestructura nomenclador: limpieza destructiva

Última fase de la reestructura del nomenclador. Requiere que:
  1. La fase 2 (código) ya esté desplegada y validada en producción — nada debe
     seguir leyendo/escribiendo las columnas que este script borra.
  2. `app/scripts/fase3_merge_codigos_duplicados.py` ya haya corrido (fusiona los
     códigos que todavía tuvieran más de una fila — si queda alguno duplicado,
     el ADD UNIQUE de abajo falla y hay que correrlo primero).

Borra de `nm_nomenclador`: `proviene_de_id` (columna muerta, nunca estuvo en el
ORM, con FK e índice propios), `obra_social_nro` + `obra_social_key` (y su
unique compuesta), `codigo_nacional`, `descripcion`, `sin_restriccion_
especialidad`, `requiere_autorizacion`, `unidades_honorarios/ayudante/gastos`.
Agrega `UNIQUE(codigo)` — ya es identidad única — y borra el índice plano
`ix_nm_nomenclador_codigo`, redundante con esa unique.

Borra la tabla `nm_nomenclador_especialidad` completa: reemplazada del todo por
`nm_valor_especialidad` desde la fase 2.

`nm_nomenclador_descripcion_legacy` NO se toca — sigue siendo el fallback de
descripción para los pares (OS, código) que nunca tuvieron su propio
`Valor.descripcion` (ver obstáculo 2 del plan de migración).

Revision ID: nn3fase3drop1
Revises: nn2fase2rel1
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "nn3fase3drop1"
down_revision: Union[str, Sequence[str], None] = "nn2fase2rel1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # proviene_de_id: columna legacy muerta (nunca estuvo mapeada), con FK a sí misma.
    op.drop_constraint("nm_nomenclador_ibfk_1", "nm_nomenclador", type_="foreignkey")
    op.drop_index("ix_nm_nomenclador_proviene_de_id", table_name="nm_nomenclador")
    op.drop_column("nm_nomenclador", "proviene_de_id")

    # obra_social_nro / obra_social_key: el código pasa a ser identidad única —
    # primero la unique compuesta (usa obra_social_key), después las columnas.
    op.drop_constraint("uq_nm_nomenclador_codigo_os", "nm_nomenclador", type_="unique")
    op.drop_index("ix_nm_nomenclador_os", table_name="nm_nomenclador")
    op.drop_column("nm_nomenclador", "obra_social_key")
    op.drop_column("nm_nomenclador", "obra_social_nro")

    # Datos que pasaron a vivir por obra social (Valor / ValorEspecialidad).
    op.drop_index("ix_nm_nomenclador_codigo_nacional", table_name="nm_nomenclador")
    op.drop_column("nm_nomenclador", "codigo_nacional")
    op.drop_column("nm_nomenclador", "descripcion")
    op.drop_column("nm_nomenclador", "sin_restriccion_especialidad")
    op.drop_index("ix_nm_nomenclador_requiere_autorizacion", table_name="nm_nomenclador")
    op.drop_column("nm_nomenclador", "requiere_autorizacion")
    op.drop_column("nm_nomenclador", "unidades_honorarios")
    op.drop_column("nm_nomenclador", "unidades_ayudante")
    op.drop_column("nm_nomenclador", "unidades_gastos")

    # codigo como identidad única — reemplaza al índice plano.
    op.drop_index("ix_nm_nomenclador_codigo", table_name="nm_nomenclador")
    op.create_unique_constraint("uq_nm_nomenclador_codigo", "nm_nomenclador", ["codigo"])

    # nm_nomenclador_especialidad: reemplazada del todo por nm_valor_especialidad.
    op.drop_table("nm_nomenclador_especialidad")


def downgrade() -> None:
    """Best-effort: recrea el esquema, NO los datos borrados (proviene_de_id,
    descripcion/unidades/etc. de nm_nomenclador, ni las filas de
    nm_nomenclador_especialidad — se perdieron en el upgrade)."""
    op.create_table(
        "nm_nomenclador_especialidad",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("nomenclador_id", sa.Integer(), nullable=False),
        sa.Column("especialidad_id_colegio", sa.Integer(), nullable=False),
        sa.Column("obra_social_nro", sa.Integer(), nullable=True),
        sa.Column(
            "obra_social_key", sa.Integer(),
            sa.Computed("coalesce(obra_social_nro, -1)", persisted=True), nullable=True,
        ),
        sa.Column("activo", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("observacion", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"),
            onupdate=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["nomenclador_id"], ["nm_nomenclador.id"]),
        sa.UniqueConstraint(
            "nomenclador_id", "especialidad_id_colegio", "obra_social_key",
            name="uq_nm_nom_esp_os",
        ),
    )
    op.create_index("ix_nm_nom_esp_os", "nm_nomenclador_especialidad", ["obra_social_nro"])
    op.create_index("ix_nm_nom_esp_especialidad", "nm_nomenclador_especialidad", ["especialidad_id_colegio"])

    op.drop_constraint("uq_nm_nomenclador_codigo", "nm_nomenclador", type_="unique")
    op.create_index("ix_nm_nomenclador_codigo", "nm_nomenclador", ["codigo"])

    op.add_column("nm_nomenclador", sa.Column("unidades_gastos", sa.DECIMAL(10, 2), nullable=True))
    op.add_column("nm_nomenclador", sa.Column("unidades_ayudante", sa.DECIMAL(10, 2), nullable=True))
    op.add_column("nm_nomenclador", sa.Column("unidades_honorarios", sa.DECIMAL(10, 2), nullable=True))
    op.add_column(
        "nm_nomenclador",
        sa.Column("requiere_autorizacion", sa.Boolean(), server_default="0", nullable=False),
    )
    op.create_index("ix_nm_nomenclador_requiere_autorizacion", "nm_nomenclador", ["requiere_autorizacion"])
    op.add_column(
        "nm_nomenclador",
        sa.Column("sin_restriccion_especialidad", sa.Boolean(), server_default="0", nullable=False),
    )
    op.add_column("nm_nomenclador", sa.Column("descripcion", sa.String(255), nullable=True))
    op.add_column("nm_nomenclador", sa.Column("codigo_nacional", sa.String(20), nullable=True))
    op.create_index("ix_nm_nomenclador_codigo_nacional", "nm_nomenclador", ["codigo_nacional"])

    op.add_column("nm_nomenclador", sa.Column("obra_social_nro", sa.Integer(), nullable=True))
    op.add_column(
        "nm_nomenclador",
        sa.Column(
            "obra_social_key", sa.Integer(),
            sa.Computed("coalesce(obra_social_nro, -1)", persisted=True), nullable=True,
        ),
    )
    op.create_index("ix_nm_nomenclador_os", "nm_nomenclador", ["obra_social_nro"])
    op.create_unique_constraint(
        "uq_nm_nomenclador_codigo_os", "nm_nomenclador", ["codigo", "obra_social_key"]
    )

    op.add_column("nm_nomenclador", sa.Column("proviene_de_id", sa.Integer(), nullable=True))
    op.create_index("ix_nm_nomenclador_proviene_de_id", "nm_nomenclador", ["proviene_de_id"])
    op.create_foreign_key(
        "nm_nomenclador_ibfk_1", "nm_nomenclador", "nm_nomenclador",
        ["proviene_de_id"], ["id"],
    )
