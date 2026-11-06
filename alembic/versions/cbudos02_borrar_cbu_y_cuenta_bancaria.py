"""Cobro del socio: borra `cbu` y `cuenta_bancaria` de `listado_medico`

Contraer: segunda mitad de `cbudos01`. La API nueva ya no mapea estas columnas (las
reemplazan `cbu_santander`, `cbu_otro_banco` y `medio_cobro`), así que se pueden borrar.
Los valores viejos quedan en la tabla `listado_medico_cbu_respaldo_20261105`.

Ya se aplicó a mano en producción el 2026-11-05 (con mysqldump antes); es idempotente.

Revision ID: cbudos02
Revises: cbudos01
Create Date: 2026-11-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "cbudos02"
down_revision: Union[str, Sequence[str], None] = "cbudos01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tiene_columna(bind, tabla: str, columna: str) -> bool:
    return columna in {c["name"] for c in sa.inspect(bind).get_columns(tabla)}


def upgrade() -> None:
    bind = op.get_bind()
    for columna in ("cbu", "cuenta_bancaria"):
        if _tiene_columna(bind, "listado_medico", columna):
            op.drop_column("listado_medico", columna)


def downgrade() -> None:
    """Vuelve a crear las columnas vacías; los valores están en el respaldo."""
    bind = op.get_bind()
    if not _tiene_columna(bind, "listado_medico", "cbu"):
        op.add_column("listado_medico", sa.Column("cbu", sa.String(50), nullable=True))
    if not _tiene_columna(bind, "listado_medico", "cuenta_bancaria"):
        op.add_column("listado_medico", sa.Column("cuenta_bancaria", sa.String(20), nullable=True))
