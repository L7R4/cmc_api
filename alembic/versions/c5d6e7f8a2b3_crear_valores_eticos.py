"""crear valores_eticos (la tabla que r5s6t7u8v9w0 nunca llegó a crear)

`r5s6t7u8v9w0` (2026-05-07) hacía dos cosas: agregar `valor_prestacion.FECHA_FINAL`
y crear `valores_eticos`. La columna está en dev y en prod, la tabla **en ninguna
de las dos** — la revisión quedó marcada como aplicada con el `create_table` sin
ejecutar. Resultado: el módulo `/api/valores-eticos` responde 500 desde siempre
(un `POST` real el 2026-08-10 lo dejó registrado en `audit_log`).

Esta revisión crea la tabla de verdad. Es idempotente: si en algún entorno ya
existiera, no hace nada.

Estructura tomada del modelo `ValoresEticos` (`app/db/models/catalogs.py`), que es
lo que el código espera. `fecha_update` va NULL y sin default: la escribe la app
(`crear_valor_etico` manda `datetime.now(timezone.utc)`), y dejarla como TIMESTAMP
implícito la convertiría en NOT NULL con auto-update según el sql_mode.

⚠ Producción no tiene `alembic_version` — el DDL equivalente está en
`docs/api/imports/2026-08-11_crear_valores_eticos.sql`.

Revision ID: c5d6e7f8a2b3
Revises: b4c5d6e7f8a1
Create Date: 2026-08-11

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = 'c5d6e7f8a2b3'
down_revision = 'b4c5d6e7f8a1'
branch_labels = None
depends_on = None


def _existe(conn) -> bool:
    return sa.inspect(conn).has_table("valores_eticos")


def upgrade() -> None:
    conn = op.get_bind()
    if _existe(conn):
        return  # ya creada a mano en ese entorno
    op.create_table(
        "valores_eticos",
        sa.Column("id", mysql.INTEGER(11), primary_key=True, autoincrement=True),
        sa.Column("pdf_path", sa.String(500), nullable=True),
        sa.Column("observaciones", sa.String(1000), nullable=True),
        sa.Column("fecha_update", sa.TIMESTAMP(), nullable=True),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    # El listado y el endpoint /ultimo ordenan por fecha_update DESC.
    op.create_index("ix_valores_eticos_fecha_update", "valores_eticos", ["fecha_update"])


def downgrade() -> None:
    conn = op.get_bind()
    if not _existe(conn):
        return
    op.drop_index("ix_valores_eticos_fecha_update", "valores_eticos")
    op.drop_table("valores_eticos")
