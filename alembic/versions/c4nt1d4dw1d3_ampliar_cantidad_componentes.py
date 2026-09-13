"""Ampliar nm_valor_componentes.cantidad de DECIMAL(10,4) a DECIMAL(16,4)

El techo viejo eran seis dígitos enteros: 999.999,9999. No alcanza.

Hay obras sociales que pactan el precio por código en vez de por unidades y lo
modelan con el galeno en `valor_unitario = 1,00`, poniendo el importe entero en
`cantidad`. Con esa forma —que es legítima y está en uso— `cantidad` **es** un
importe, así que tiene que poder representar lo mismo que las columnas de dinero
del módulo: `nm_galenos.valor_unitario`, `nm_valor_componentes.valor_unitario` y
`nm_historial_precio_codigo.precio_total` son todas `DECIMAL(14,2)`, o sea 12
dígitos enteros. `DECIMAL(16,4)` da esos mismos 12, conservando los 4 decimales
que necesitan las unidades fraccionarias (1,5 galenos, 112,5 de ayudante).

Por qué importaba: pasarse del techo **no fallaba en producción**. El `sql_mode`
de prod no incluye `STRICT_TRANS_TABLES`, así que MySQL clavaba el valor en el
máximo y devolvía OK; dev sí está en strict y abortaba. El ensayo atrapaba el
problema y la carga real lo tragaba.

643 componentes de la obra social 81 quedaron en 999.999,9999 con valores reales
de hasta 4.522.375. `precio_total` no se vio afectado porque se calcula en Python
antes del INSERT, pero cualquier regeneración del historial (una rotación de
galeno, la operación más común del módulo) los habría recalculado desde el
componente truncado. Se restauraron desde `componentes_snapshot`, que había
guardado la cantidad verdadera.

Ampliar un DECIMAL es una operación que no pierde datos. El `downgrade` sí puede
perderlos —vuelve a truncar lo que exceda el techo viejo—, así que aborta si
encuentra alguna fila que no entre.

Revision ID: c4nt1d4dw1d3
Revises: 07026af30714
Create Date: 2026-08-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4nt1d4dw1d3"
down_revision: Union[str, None] = "07026af30714"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TECHO_VIEJO = "999999.9999"


def upgrade() -> None:
    op.alter_column(
        "nm_valor_componentes",
        "cantidad",
        existing_type=sa.DECIMAL(10, 4),
        type_=sa.DECIMAL(16, 4),
        existing_nullable=False,
        existing_server_default=sa.text("0.0000"),
    )


def downgrade() -> None:
    # Estrechar sí destruye: cualquier cantidad por encima del techo viejo se
    # truncaría, y en un servidor sin STRICT_TRANS_TABLES lo haría en silencio.
    # Mejor negarse que perder precios.
    conn = op.get_bind()
    excedidas = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM nm_valor_componentes WHERE cantidad > :techo"
        ),
        {"techo": TECHO_VIEJO},
    ).scalar()
    if excedidas:
        raise RuntimeError(
            f"No se puede volver a DECIMAL(10,4): hay {excedidas} filas con "
            f"cantidad mayor a {TECHO_VIEJO} que quedarían truncadas. "
            f"Resolvelas antes de bajar esta migración."
        )
    op.alter_column(
        "nm_valor_componentes",
        "cantidad",
        existing_type=sa.DECIMAL(16, 4),
        type_=sa.DECIMAL(10, 4),
        existing_nullable=False,
        existing_server_default=sa.text("0.0000"),
    )
