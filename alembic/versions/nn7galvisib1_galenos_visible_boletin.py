"""nm_galenos: agrega `visible` (mostrar el galeno en el boletín del médico)

`visible = 0` oculta el galeno de esa OS en "Valores Boletín" del socio
(`/panel/boletin-valores`). No afecta precios, facturación ni liquidación.
Se togglea por (OS, código) desde "Actualizar Unidades" y se aplica a todas
las filas (niveles y vigencias); las rotaciones de precio lo heredan.

Revision ID: nn7galvisib1
Revises: nn6plantesp1
Create Date: 2026-10-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "nn7galvisib1"
down_revision: Union[str, Sequence[str], None] = "nn6plantesp1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "nm_galenos",
        sa.Column("visible", sa.Boolean(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("nm_galenos", "visible")
