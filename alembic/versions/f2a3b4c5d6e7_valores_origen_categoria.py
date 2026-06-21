"""valores_origen_categoria

Agrega el eje de **origen / categoría** a los valores del nomenclador (NE > NNE > NN).
El `origen` define la prioridad del lookup (la prioridad vive en código:
``service.ORIGEN_PRIORIDAD``); en DB solo se guarda el tag como VARCHAR(10) para poder
sumar orígenes sin migración.

- Reinicio de datos pactados (igual que v3): vacía nm_historial_precio_codigo,
  nm_valor_componentes, nm_valores y nm_galenos. El catálogo (nm_nomenclador), el
  homologador y las habilitaciones se conservan. Se recargan los datos con `origen`.
- nm_valores: + columna `origen` (NOT NULL) + índice.
- nm_historial_precio_codigo: + columna `origen` (NOT NULL); el UNIQUE de variante
  pasa a incluir `origen`: (nomenclador_id, obra_social_nro, origen, especialidad_key,
  vigencia_desde).

Revision ID: f2a3b4c5d6e7
Revises: a1c2e3f40506
Create Date: 2026-06-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, Sequence[str], None] = "a1c2e3f40506"
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

    # ── nm_valores: columna origen ───────────────────────────────────────────
    op.add_column(
        "nm_valores",
        sa.Column("origen", sa.String(length=10), nullable=False),
    )
    op.create_index("ix_nm_valores_origen", "nm_valores", ["origen"])

    # ── nm_historial_precio_codigo: origen + unique de variante con origen ────
    op.add_column(
        "nm_historial_precio_codigo",
        sa.Column("origen", sa.String(length=10), nullable=False),
    )
    op.drop_constraint("uq_nm_historial_precio", "nm_historial_precio_codigo", type_="unique")
    op.create_unique_constraint(
        "uq_nm_historial_precio",
        "nm_historial_precio_codigo",
        ["nomenclador_id", "obra_social_nro", "origen", "especialidad_key", "vigencia_desde"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_nm_historial_precio", "nm_historial_precio_codigo", type_="unique")
    op.create_unique_constraint(
        "uq_nm_historial_precio",
        "nm_historial_precio_codigo",
        ["nomenclador_id", "obra_social_nro", "especialidad_key", "vigencia_desde"],
    )
    op.drop_column("nm_historial_precio_codigo", "origen")

    op.drop_index("ix_nm_valores_origen", table_name="nm_valores")
    op.drop_column("nm_valores", "origen")
    # NOTA: los datos vaciados en upgrade() no se pueden restaurar
