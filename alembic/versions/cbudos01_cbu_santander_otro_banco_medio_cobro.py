"""Cobro del socio: `cbu_santander`, `cbu_otro_banco` y `medio_cobro` en `listado_medico`

Reemplazan a `cbu` (un solo CBU para todo) y `cuenta_bancaria` (un texto que la API
leía pero que nadie cargó nunca: está en NULL en todas las filas).

* `cbu_santander`   CBU de una cuenta del Banco Santander (entidad 072).
* `cbu_otro_banco`  CBU de cualquier otro banco. Puede traer un valor viejo que no es un CBU
                    (la cuenta HSBC de 10 dígitos del sistema anterior): se conserva con
                    `medio_cobro = 'cheque'`.
* `medio_cobro`     por dónde se le paga: `santander` / `otros_bancos` / `cheque`.

Esta revisión SOLO agrega las columnas (expandir). Las columnas viejas se borran en otra
revisión, cuando la API nueva ya está en producción: si se borraran antes, la API vieja se
caería en cada consulta a `listado_medico`. Los datos se pasan con
`scripts/migracion_legacy_29oct/e05_cbu_dos_columnas.py`, no desde acá.

Revision ID: cbudos01
Revises: agendaavisos01
Create Date: 2026-11-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "cbudos01"
down_revision: Union[str, Sequence[str], None] = "agendaavisos01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tiene_columna(bind, tabla: str, columna: str) -> bool:
    return columna in {c["name"] for c in sa.inspect(bind).get_columns(tabla)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _tiene_columna(bind, "listado_medico", "cbu_santander"):
        op.add_column("listado_medico", sa.Column("cbu_santander", sa.String(22), nullable=True))
    if not _tiene_columna(bind, "listado_medico", "cbu_otro_banco"):
        op.add_column("listado_medico", sa.Column("cbu_otro_banco", sa.String(22), nullable=True))
    if not _tiene_columna(bind, "listado_medico", "medio_cobro"):
        op.add_column(
            "listado_medico",
            sa.Column(
                "medio_cobro",
                sa.Enum("santander", "otros_bancos", "cheque", name="medio_cobro_enum"),
                nullable=False,
                server_default="cheque",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    for columna in ("medio_cobro", "cbu_otro_banco", "cbu_santander"):
        if _tiene_columna(bind, "listado_medico", columna):
            op.drop_column("listado_medico", columna)
