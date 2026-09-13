"""add creado_por/creado_en a facturacion

Revision ID: d1e2f3a4b5c6
Revises: a57121d65f28
Create Date: 2026-09-07

Escrita a mano, no autogenerate (mismo motivo que a57121d65f28: evitar drift
preexistente ajeno a este cambio). Agrega dos columnas nullable a `facturacion`
para poder distinguir "creado por" (alta de la cabecera, primera prestación
cargada o alta de un complemento) de "cerrado por" — que ya es, de hecho,
`usuario`/`fecha`, pisados en `cerrar_periodo`. No agrega índices: no hay un
acceso por estas columnas todavía que lo justifique.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'a57121d65f28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('facturacion', sa.Column('creado_por', sa.String(length=30), nullable=True))
    op.add_column('facturacion', sa.Column('creado_en', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('facturacion', 'creado_en')
    op.drop_column('facturacion', 'creado_por')
