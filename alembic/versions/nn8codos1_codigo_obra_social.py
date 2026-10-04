"""nm_codigo_obra_social (alta del código en la O.S.) + detalle_facturacion.sin_valorizar

Etapa 3 del flujo del nomenclador: el código dado de alta en una obra social,
con o sin precio. Backfill: un par por cada (obra social, código) que hoy tiene
algún `nm_valores` activo, con los datos del par tomados de la primera variante
(NE antes que NN, después menor id) y "sin restricción" si alguna fila activa lo
tiene (mismo criterio que `service.par_sin_restriccion`).

Solo agrega (tabla + columna nullable): el código de `main` sigue funcionando
contra una base migrada.

Revision ID: nn8codos1
Revises: nn7galvisib1
Create Date: 2026-10-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "nn8codos1"
down_revision: Union[str, Sequence[str], None] = "nn7galvisib1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BACKFILL = """
INSERT INTO nm_codigo_obra_social
    (obra_social_nro, nomenclador_id, codigo, descripcion, categoria, complejidad,
     requiere_autorizacion, cantidad_ayudantes, sin_restriccion_especialidad,
     observacion, estado, creado_por)
SELECT v.obra_social_nro, v.nomenclador_id, v.codigo, v.descripcion, v.categoria,
       v.complejidad, v.requiere_autorizacion, v.cantidad_ayudantes, b.sin_restriccion,
       v.observacion, 'activo', 'backfill'
FROM nm_valores v
JOIN (
    SELECT obra_social_nro, nomenclador_id,
           MIN(CASE WHEN origen = 'NE' THEN id END) AS ne_id,
           MIN(id) AS cualquier_id,
           MAX(sin_restriccion_especialidad) AS sin_restriccion
    FROM nm_valores
    WHERE estado = 'activo'
    GROUP BY obra_social_nro, nomenclador_id
) b ON v.id = COALESCE(b.ne_id, b.cualquier_id)
"""


def upgrade() -> None:
    op.create_table(
        "nm_codigo_obra_social",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("obra_social_nro", sa.Integer(), nullable=False),
        sa.Column("nomenclador_id", sa.Integer(), sa.ForeignKey("nm_nomenclador.id"), nullable=False),
        sa.Column("codigo", sa.String(20), nullable=False),
        sa.Column("descripcion", sa.String(255), nullable=True),
        sa.Column("categoria", sa.String(100), nullable=True),
        sa.Column("complejidad", sa.Enum("baja", "media", "alta", name="nm_cos_complejidad_enum"), nullable=True),
        sa.Column("requiere_autorizacion", sa.Boolean(), nullable=True),
        sa.Column("cantidad_ayudantes", sa.Integer(), nullable=True),
        sa.Column("sin_restriccion_especialidad", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("observacion", sa.Text(), nullable=True),
        sa.Column("estado", sa.Enum("activo", "suspendido", name="nm_cos_estado_enum"),
                  nullable=False, server_default="activo"),
        sa.Column("creado_por", sa.String(30), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("obra_social_nro", "nomenclador_id", name="uq_nm_codigo_os"),
    )
    op.create_index("ix_nm_codigo_os_os_codigo", "nm_codigo_obra_social", ["obra_social_nro", "codigo"])
    op.create_index("ix_nm_codigo_os_nomenclador", "nm_codigo_obra_social", ["nomenclador_id"])
    op.execute(BACKFILL)

    op.add_column("detalle_facturacion", sa.Column("sin_valorizar", sa.String(3), nullable=True))


def downgrade() -> None:
    op.drop_column("detalle_facturacion", "sin_valorizar")
    op.drop_index("ix_nm_codigo_os_nomenclador", table_name="nm_codigo_obra_social")
    op.drop_index("ix_nm_codigo_os_os_codigo", table_name="nm_codigo_obra_social")
    op.drop_table("nm_codigo_obra_social")
