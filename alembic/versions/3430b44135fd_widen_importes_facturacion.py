"""widen_importes_facturacion

Revision ID: 3430b44135fd
Revises: c9d0e1f2a3b4
Create Date: 2026-07-21

Ensancha las columnas de dinero de `facturacion` y `detalle_facturacion` de
`DOUBLE(10,2)` a `DECIMAL(14,2)`.

Motivo: `DOUBLE(10,2)` topea en 99.999.999,99 y ya hay facturas reales por encima
de ese valor — 8 cabeceras de las OS 8 y 411 quedaron clampeadas en la DB (la real
más grande es $122.349.666,98 en 202505/OS 8). El import desde la base de CMC
recalcula `facturacion.importe` como la suma del detalle, así que sin este cambio
el recálculo volvería a truncar justo las facturas de mayor monto.

Además alinea la DB con lo que el ORM ya declaraba: `FacturacionCMC.importe` y
`DetalleFacturacionCMC.honorarios/gastos/ayudante/importe_total` están mapeadas
como `DECIMAL(14, 2)` en `app/db/models/cmc_facturacion.py` desde siempre.

DECIMAL además es exacto: `DOUBLE` es binario y arrastra error de redondeo en las
sumas de importes, que es exactamente lo que hacen `obtener_factura_detalle` y el
recálculo de cabeceras.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "3430b44135fd"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (tabla, columna) de todas las columnas de dinero afectadas.
_COLUMNAS = [
    ("facturacion", "importe"),
    ("detalle_facturacion", "honorarios"),
    ("detalle_facturacion", "gastos"),
    ("detalle_facturacion", "ayudante"),
    ("detalle_facturacion", "importe_total"),
]


def _alterar(tipo_actual: sa.types.TypeEngine, tipo_nuevo: sa.types.TypeEngine) -> None:
    # Ambas son tablas legacy MyISAM con zero-dates ('0000-00-00') que rompen la
    # revalidación del ALTER bajo sql_mode estricto — se relaja la sesión solo para
    # este cambio (mismo patrón que las migraciones `c9d0e1f2a3b4` y `6e77c0e80eba`).
    conn = op.get_bind()
    original_mode = conn.execute(sa.text("SELECT @@SESSION.sql_mode")).scalar()
    conn.execute(sa.text("SET SESSION sql_mode = ''"))
    try:
        for tabla, columna in _COLUMNAS:
            op.alter_column(
                tabla,
                columna,
                existing_type=tipo_actual,
                type_=tipo_nuevo,
                existing_nullable=False,
            )
    finally:
        conn.execute(sa.text("SET SESSION sql_mode = :mode"), {"mode": original_mode})


def upgrade() -> None:
    _alterar(
        sa.dialects.mysql.DOUBLE(precision=10, scale=2),
        sa.DECIMAL(precision=14, scale=2),
    )


def downgrade() -> None:
    # Revertir puede truncar: cualquier importe > 99.999.999,99 cargado mientras la
    # columna era DECIMAL(14,2) se clampea al bajar. Es la razón de ser de la migración.
    _alterar(
        sa.DECIMAL(precision=14, scale=2),
        sa.dialects.mysql.DOUBLE(precision=10, scale=2),
    )
