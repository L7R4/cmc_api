"""afiliado_dni_alfanumerico

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-07-28

Ensancha `afiliado.dni` de varchar(15) a varchar(20).

El campo identifica al paciente por DNI **o** por nro de afiliado de la obra
social, que es alfanumérico y puede llevar separadores (ej. "1231233/00").
Se alinea con `detalle_facturacion.dni_p`, que ya es varchar(20) en la DB y es
donde se copia el valor al cargar una prestación — sin esto, un identificador
de más de 15 caracteres entraba en la prestación pero no podía registrarse en
el padrón.

Sólo se amplía el ancho: no cambia la unicidad ni el tipo, así que no hay
riesgo para las filas existentes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b5c6d7e8f9a0"
down_revision: Union[str, None] = "a4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "afiliado", "dni",
        existing_type=sa.String(15), type_=sa.String(20),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Puede fallar/truncar si ya se cargaron identificadores de más de 15 caracteres.
    op.alter_column(
        "afiliado", "dni",
        existing_type=sa.String(20), type_=sa.String(15),
        existing_nullable=False,
    )
