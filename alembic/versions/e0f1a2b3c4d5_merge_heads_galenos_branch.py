"""merge_heads_galenos_branch

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4, f1bae5c76541
Create Date: 2026-06-13

Unifica los dos heads que convivían en el repo:
  - d9e0f1a2b3c4 (galenos_codigo_autoslug — lineaje nomenclador/valores)
  - f1bae5c76541 (merge_heads — lineaje obras_sociales/noticias/sync)

El lineaje f1bae5c76541 estaba parcialmente aplicado en la DB; antes de este
merge se reconcilió el esquema contra los modelos (alta de noticias.badge,
índices de listado_medico, drop de periodos_doctor.USUARIO) y se marcó su head
como aplicado. Este merge no ejecuta DDL: solo colapsa el grafo a un único head.
"""
from typing import Sequence, Union

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision: str = "e0f1a2b3c4d5"
down_revision: Union[str, Sequence[str], None] = ("d9e0f1a2b3c4", "f1bae5c76541")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
