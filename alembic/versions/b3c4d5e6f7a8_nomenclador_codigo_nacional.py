"""nomenclador_codigo_nacional

Agrega la columna `codigo_nacional` (VARCHAR(20), nullable) a `nm_nomenclador`.
Guarda el código del Nomenclador Nacional al que corresponde cada código del Colegio.
NULL = no identificado como nacional. El marcado de datos se hace por fuera de la
migración (scripts/mark_codigo_nacional.sql, generado desde la comparación con el PDF)
para no acoplar el esquema a un artefacto de docs/.

Migración ESCRITA A MANO de forma aditiva (el autogenerate del repo arrastra diffs
destructivos por drift modelos↔DB legacy — ver 7619682b5ae9).

Revision ID: b3c4d5e6f7a8
Revises: f2a3b4c5d6e7
Create Date: 2026-06-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (aditivo)."""
    op.add_column(
        'nm_nomenclador',
        sa.Column('codigo_nacional', sa.String(length=20), nullable=True),
    )
    op.create_index(
        'ix_nm_nomenclador_codigo_nacional', 'nm_nomenclador', ['codigo_nacional'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_nm_nomenclador_codigo_nacional', table_name='nm_nomenclador')
    op.drop_column('nm_nomenclador', 'codigo_nacional')
