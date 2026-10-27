"""dia_corte_ultimo_dia

`obras_sociales.dia_corte` deja de tener "1 = mes completo": desde el 28 en adelante es
"último día del mes" y se guarda como 31 (el corte se recorta al largo de cada mes).
Las filas existentes con 1 (mes completo, p. ej. OS 151) o con 28-30 pasan a 31.

Hay que correrla ANTES de desplegar la API nueva: sin el caso especial del 1, un `1`
viejo se leería como "del 1 al 1".

Revision ID: diacorte31
Revises: docdig001
Create Date: 2026-10-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "diacorte31"
down_revision: Union[str, Sequence[str], None] = "docdig001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE obras_sociales SET dia_corte = 31 WHERE dia_corte = 1 OR dia_corte >= 28")


def downgrade() -> None:
    # No se puede saber cuáles eran 1 y cuáles 28-30: el 31 sigue siendo válido.
    pass
