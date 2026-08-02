"""Validaciones con obras sociales: columnas nuevas en `detalle_facturacion`.

Las prestaciones que el médico valida contra una obra social se guardan en
`detalle_facturacion` con `origen_carga='medico'`, en el período que devuelve
`periodo_medico_actual` para esa obra social — la misma tabla y el mismo puntero
que usa la carga del médico desde facturación, así entran derecho a la
liquidación sin ningún volcado posterior.

El módulo **no crea tablas propias**. Lo único que hace falta son las columnas
que el circuito de facturación no tenía dónde guardar:

| Columna | Para qué |
|---|---|
| `validacion_estado` | `autorizada` · `rechazada` · `pendiente` · `cargada`. NULL = la fila no vino de una validación |
| `validacion_detalle` | texto crudo que devolvió la O.S., sin interpretar |
| `validacion_respuesta` | traza (mensaje enviado + respuesta + modo); soporte |
| `validacion_anulada` | baja lógica desde el panel del prestador |
| `coseguro` | lo que paga el afiliado; ya descontado de `importe_total` |
| `orden_path` | orden/receta en PDF adjunta por el prestador |

Todo es aditivo y nullable/con default: las filas existentes (carga del Colegio,
importación CMC) quedan con `validacion_estado = NULL` y no cambian en nada.

Revision ID: y8z9a0b1c2d3
Revises: x7y8z9a0b1c2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "y8z9a0b1c2d3"
down_revision: Union[str, None] = "x7y8z9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNAS = (
    sa.Column("validacion_estado", sa.String(length=12), nullable=True),
    sa.Column("validacion_detalle", sa.String(length=255), nullable=True),
    sa.Column("validacion_respuesta", sa.JSON(), nullable=True),
    sa.Column("validacion_anulada", sa.Boolean(), nullable=False, server_default="0"),
    sa.Column("coseguro", sa.DECIMAL(14, 2), nullable=False, server_default="0"),
    sa.Column("orden_path", sa.String(length=300), nullable=True),
)


def upgrade() -> None:
    # Fechas '0000-00-00' legacy en detalle_facturacion rompen ALTER en modo estricto.
    op.execute("SET SESSION sql_mode=''")
    for col in COLUMNAS:
        op.add_column("detalle_facturacion", col.copy())
    # El listado del prestador filtra por (cod_med, cod_obr, periodo) + validación.
    op.create_index(
        "ix_detalle_facturacion_validacion",
        "detalle_facturacion",
        ["cod_med", "cod_obr", "periodo", "validacion_estado"],
    )


def downgrade() -> None:
    op.execute("SET SESSION sql_mode=''")
    op.drop_index("ix_detalle_facturacion_validacion", table_name="detalle_facturacion")
    for col in COLUMNAS:
        op.drop_column("detalle_facturacion", col.name)
