"""nomenclador_por_obra_social

El código del nomenclador deja de ser identidad global. `nm_nomenclador` gana el eje
de pertenencia `obra_social_nro` (NULL = código del Colegio/Nacional, compartido por
todas las OS; N = código propio de esa obra social) y la unicidad pasa de `codigo` a
`(codigo, obra_social_key)`, con la columna generada `obra_social_key = COALESCE(
obra_social_nro, -1)` — mismo patrón que `nm_galenos.nivel_key`.

Complementos:
- `nm_valores.categoria` — override por OS de `nm_nomenclador.categoria`, que decide el
  `tipo` de la prestación y si los gastos se fuerzan a 0 bajo sanatorio.
- `detalle_facturacion.nomenclador_id` — a partir de acá `cod_nom` solo desambigua junto
  con `cod_obr`; se backfillea contra el catálogo compartido.

Migración sin movimiento de datos: todas las filas existentes quedan compartidas
(`obra_social_nro IS NULL`) y el sistema se comporta igual. Los códigos se separan por
OS después, uno por uno, con `POST /nomenclador/{id}/desacoplar/{obra_social_nro}`.

⚠ Producción no tiene `alembic_version` — el DDL equivalente para aplicar allá está en
`docs/api/imports/2026-08-10_nomenclador_por_obra_social.sql`.

Revision ID: e1f2a3b4c5d6
Revises: s3s10n3sr3v0
Create Date: 2026-08-10

"""
from alembic import op
import sqlalchemy as sa

revision = 'e1f2a3b4c5d6'
down_revision = 's3s10n3sr3v0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── nm_nomenclador: eje de pertenencia por obra social ───────────────────
    op.add_column(
        "nm_nomenclador",
        sa.Column("obra_social_nro", sa.Integer(), nullable=True),
    )
    # nullable=True a propósito: MariaDB (producción) rechaza NOT NULL en columnas
    # generadas — `ADD COLUMN x INT AS (...) STORED NOT NULL` es error de sintaxis allá,
    # en cualquiera de los dos órdenes. COALESCE nunca devuelve NULL, así que la
    # restricción no aporta nada y así el DDL es idéntico en MySQL y MariaDB.
    op.add_column(
        "nm_nomenclador",
        sa.Column(
            "obra_social_key",
            sa.Integer(),
            sa.Computed("coalesce(obra_social_nro, -1)", persisted=True),
            nullable=True,
        ),
    )
    # El unique viejo (solo `codigo`) es justamente la restricción que impedía que dos
    # OS tuvieran el mismo número para prácticas distintas.
    op.drop_constraint("uq_nm_nomenclador_codigo", "nm_nomenclador", type_="unique")
    op.create_unique_constraint(
        "uq_nm_nomenclador_codigo_os",
        "nm_nomenclador",
        ["codigo", "obra_social_key"],
    )
    op.create_index("ix_nm_nomenclador_os", "nm_nomenclador", ["obra_social_nro"])

    # ── nm_valores: override de categoría por OS ─────────────────────────────
    op.add_column(
        "nm_valores",
        sa.Column("categoria", sa.String(length=100), nullable=True),
    )

    # ── detalle_facturacion: a qué fila del catálogo se cotizó ───────────────
    op.add_column(
        "detalle_facturacion",
        sa.Column("nomenclador_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_detalle_facturacion_nomenclador", "detalle_facturacion", ["nomenclador_id"]
    )
    # Backfill: al momento de correr esto todo el catálogo es compartido, así que el
    # match por código es unívoco. Las filas cuyo código ya no existe en el catálogo
    # quedan en NULL — hoy tampoco resuelven descripción, no es una regresión.
    op.execute(
        """
        UPDATE detalle_facturacion d
          JOIN nm_nomenclador n
            ON n.codigo = d.cod_nom AND n.obra_social_nro IS NULL
           SET d.nomenclador_id = n.id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_detalle_facturacion_nomenclador", "detalle_facturacion")
    op.drop_column("detalle_facturacion", "nomenclador_id")
    op.drop_column("nm_valores", "categoria")
    op.drop_index("ix_nm_nomenclador_os", "nm_nomenclador")
    op.drop_constraint("uq_nm_nomenclador_codigo_os", "nm_nomenclador", type_="unique")
    op.drop_column("nm_nomenclador", "obra_social_key")
    op.drop_column("nm_nomenclador", "obra_social_nro")
    # Falla a propósito si ya se desacopló algún código por OS: restaurar la unicidad
    # global significaría perder filas.
    op.create_unique_constraint(
        "uq_nm_nomenclador_codigo", "nm_nomenclador", ["codigo"]
    )
