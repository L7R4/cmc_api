"""detalle_facturacion_via

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-07-30

Agrega `detalle_facturacion.via` ('T' tradicional / 'L' laparoscópica / NULL =
no aplica). Reemplaza el diseño anterior de "un código por técnica"
(`nm_nomenclador.proviene_de_id`, ver migración `d8e9f0a1b2c3`): la vía pasa a
ser un atributo de la prestación, y el precio laparoscópico se deriva a
demanda en `app/modules/nomenclador/service_vias.py` a partir del galeno —
sin crear códigos ni valores nuevos.

No se tocan las columnas legacy CMC `urgencia`/`nro_vias` (sin uso en la API).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, None] = "b5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tabla legacy con zero-dates ('0000-00-00') que rompen la revalidación del
    # ALTER bajo sql_mode estricto — se relaja la sesión solo para este cambio
    # (mismo patrón que `a4b5c6d7e8f9` / `e6f7a8b9c0d1` / `3430b44135fd`).
    conn = op.get_bind()
    original_mode = conn.execute(sa.text("SELECT @@SESSION.sql_mode")).scalar()
    conn.execute(sa.text("SET SESSION sql_mode = ''"))
    try:
        op.add_column(
            "detalle_facturacion",
            sa.Column("via", sa.String(length=1), nullable=True),
        )
    finally:
        conn.execute(sa.text("SET SESSION sql_mode = :mode"), {"mode": original_mode})


def downgrade() -> None:
    op.drop_column("detalle_facturacion", "via")
