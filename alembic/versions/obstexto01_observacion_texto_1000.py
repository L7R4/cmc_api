"""Ampliar boletin_observacion.texto de 400 a 1000 caracteres.

Todos los renglones de la observación de una obra social van en una sola columna; con 400 no entraban
más de dos o tres condiciones. Solo ensancha: si la columna ya tiene 1000 o más no hace nada, y el
downgrade no achica (cortaría o rechazaría observaciones ya guardadas). La tabla de plantillas queda
igual: su texto es UNIQUE y con 1000 caracteres superaría el largo máximo de la clave.

Revision ID: obstexto01
Revises: lotefact02
Create Date: 2026-11-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "obstexto01"
down_revision: Union[str, Sequence[str], None] = "lotefact02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLA = "boletin_observacion"
NUEVO = 1000


def _largo_actual() -> int | None:
    for c in sa.inspect(op.get_bind()).get_columns(TABLA):
        if c["name"] == "texto":
            return getattr(c["type"], "length", None)
    return None


def upgrade() -> None:
    largo = _largo_actual()
    if largo is None or largo >= NUEVO:
        return
    op.alter_column(
        TABLA,
        "texto",
        existing_type=sa.String(largo),
        type_=sa.String(NUEVO),
        existing_nullable=False,
        existing_server_default=sa.text("''"),
    )


def downgrade() -> None:
    # No se achica: perdería texto ya cargado.
    pass
