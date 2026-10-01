"""nm_plantilla_especialidad_codigo: especialidades sugeridas por código + backfill desde espe_cod

Tabla nueva con la plantilla de especialidades sugeridas de cada código del
Colegio, clave única (codigo, especialidad_id_colegio). Es sólo una sugerencia:
la habilitación real sigue en `nm_valor_especialidad` y se escribe al aplicar la
plantilla a una obra social.

Backfill desde la tabla legacy `espe_cod` (CODIGO varchar(8), ID_ESPE):
- `ID_ESPE` coincide con `especialidad.ID_COLEGIO_ESPE` (no con `especialidad.ID`);
  los que no existen en el catálogo de especialidades se descartan.
- Sólo códigos que existen en `nm_nomenclador`.
- Deduplica (hay pares repetidos en el origen).
- `espe_cod` usa otra collation que `nm_nomenclador`: el JOIN fuerza una común.

Revision ID: nn6plantesp1
Revises: nn5sinrest1
Create Date: 2026-09-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "nn6plantesp1"
down_revision: Union[str, Sequence[str], None] = "nn5sinrest1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "nm_plantilla_especialidad_codigo",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("codigo", sa.String(20), nullable=False),
        sa.Column("especialidad_id_colegio", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "codigo", "especialidad_id_colegio", name="uq_nm_plantilla_esp_codigo"
        ),
    )
    op.create_index("ix_nm_plantilla_esp_codigo", "nm_plantilla_especialidad_codigo", ["codigo"])

    op.execute(
        """
        INSERT INTO nm_plantilla_especialidad_codigo (codigo, especialidad_id_colegio)
        SELECT DISTINCT n.codigo, s.ID_COLEGIO_ESPE
        FROM espe_cod e
        JOIN nm_nomenclador n
          ON n.codigo COLLATE utf8_general_ci = e.CODIGO COLLATE utf8_general_ci
        JOIN especialidad s ON s.ID_COLEGIO_ESPE = e.ID_ESPE
        """
    )


def downgrade() -> None:
    op.drop_index("ix_nm_plantilla_esp_codigo", table_name="nm_plantilla_especialidad_codigo")
    op.drop_table("nm_plantilla_especialidad_codigo")
