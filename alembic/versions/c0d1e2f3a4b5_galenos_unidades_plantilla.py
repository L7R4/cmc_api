"""galenos_unidades_plantilla

Agrega a `nm_galenos` las unidades-plantilla por concepto (por OS):
`unidades_honorarios`, `unidades_ayudante`, `unidades_gastos` — DECIMAL(10,2), nullable.

El galeno pasa a funcionar como plantilla: al asociar un galeno a un código con un
ValorComponente calculable (cantidad=0), el pre-fill toma estas unidades. Precedencia:
cantidad explícita > unidad del galeno > nm_nomenclador.unidades_* (NN).

Migración aditiva: sin reinicio de datos, compatible con galenos existentes (las 3
columnas quedan NULL → el pre-fill sigue cayendo al nomenclador como hasta ahora).

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-06-25 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c0d1e2f3a4b5'
down_revision: Union[str, Sequence[str], None] = 'b9c0d1e2f3a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('nm_galenos', sa.Column('unidades_honorarios', sa.DECIMAL(10, 2), nullable=True))
    op.add_column('nm_galenos', sa.Column('unidades_ayudante', sa.DECIMAL(10, 2), nullable=True))
    op.add_column('nm_galenos', sa.Column('unidades_gastos', sa.DECIMAL(10, 2), nullable=True))


def downgrade() -> None:
    op.drop_column('nm_galenos', 'unidades_gastos')
    op.drop_column('nm_galenos', 'unidades_ayudante')
    op.drop_column('nm_galenos', 'unidades_honorarios')
