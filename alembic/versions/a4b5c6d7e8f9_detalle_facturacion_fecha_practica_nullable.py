"""detalle_facturacion_fecha_practica_nullable

Revision ID: a4b5c6d7e8f9
Revises: 9f1a2b3c4d5e
Create Date: 2026-07-28

Hace nullable `detalle_facturacion.fecha_practica`. La migración
`e6f7a8b9c0d1` (columnas legacy nullable) la había dejado deliberadamente
NOT NULL porque en ese momento el Colegio SIEMPRE la completaba (se rellenaba
con el primer día del período si el operador no la cargaba).

Ese comportamiento cambió: en cargas por `cantidad` (equipo/lote) el operador
no puede asignarle una fecha individual a cada unidad, así que ahora se
guarda NULL en vez de inventar una fecha (ver `service.fecha_para_precio` /
`_insertar_prestaciones`, módulo facturación). El ORM ya mapeaba la columna
como `Optional[date]` — sin este ALTER, insertar sin fecha rompía con
IntegrityError (500) porque la DB seguía exigiendo NOT NULL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, None] = "9f1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tabla legacy con zero-dates ('0000-00-00') que rompen la revalidación del
    # ALTER bajo sql_mode estricto — se relaja la sesión solo para este cambio
    # (mismo patrón que `e6f7a8b9c0d1` y `3430b44135fd`).
    conn = op.get_bind()
    original_mode = conn.execute(sa.text("SELECT @@SESSION.sql_mode")).scalar()
    conn.execute(sa.text("SET SESSION sql_mode = ''"))
    try:
        op.alter_column(
            "detalle_facturacion", "fecha_practica",
            existing_type=sa.Date(), nullable=True,
        )
    finally:
        conn.execute(sa.text("SET SESSION sql_mode = :mode"), {"mode": original_mode})


def downgrade() -> None:
    op.alter_column(
        "detalle_facturacion", "fecha_practica",
        existing_type=sa.Date(), nullable=False,
    )
