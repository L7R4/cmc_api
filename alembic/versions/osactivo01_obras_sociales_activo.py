"""obras_sociales: MARCA ('S'/'N') pasa a `activo` (booleano) y se elimina VER_VALOR

`activo = 1` es lo que antes era `MARCA <> 'N'`; `activo = 0`, la obra social dada de baja.
`VER_VALOR` no lo lee nadie en el sistema nuevo (era del buscador de valores del legacy).

Mismo DDL que `scripts/obras_sociales_activo_2026-10-10.sql` (producción no trackea
alembic_version, ahí se aplica a mano).

Revision ID: osactivo01
Revises: nn9nivelados1
Create Date: 2026-10-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "osactivo01"
down_revision: Union[str, Sequence[str], None] = "nn9nivelados1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _sin_modo_estricto() -> None:
    # `condicion_iva` es ENUM('responsable_inscripto','exento','','') (el '' repetido es un defecto
    # viejo) y MySQL en modo estricto rechaza cualquier ALTER de la tabla por eso (error 1291).
    op.execute("SET SESSION sql_mode = REPLACE(@@SESSION.sql_mode, 'STRICT_TRANS_TABLES', '')")


def upgrade() -> None:
    _sin_modo_estricto()
    op.add_column(
        "obras_sociales",
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("1")),
    )
    op.execute("UPDATE obras_sociales SET activo = (MARCA <> 'N')")
    op.drop_index("MARCA", table_name="obras_sociales")
    op.drop_column("obras_sociales", "MARCA")
    op.drop_column("obras_sociales", "VER_VALOR")
    op.create_index("ix_obras_sociales_activo", "obras_sociales", ["activo"])


def downgrade() -> None:
    _sin_modo_estricto()
    # VER_VALOR no se recupera: vuelve en 'N'.
    op.add_column(
        "obras_sociales",
        sa.Column("MARCA", sa.String(1, collation="utf8_spanish2_ci"), nullable=False, server_default="N"),
    )
    op.add_column(
        "obras_sociales",
        sa.Column("VER_VALOR", sa.String(1, collation="utf8_spanish2_ci"), nullable=False, server_default="N"),
    )
    op.execute("UPDATE obras_sociales SET MARCA = IF(activo = 1, 'S', 'N')")
    op.drop_index("ix_obras_sociales_activo", table_name="obras_sociales")
    op.drop_column("obras_sociales", "activo")
    op.create_index("MARCA", "obras_sociales", ["MARCA"])
