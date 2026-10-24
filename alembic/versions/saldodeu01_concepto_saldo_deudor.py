"""Concepto «Saldo deudor de débitos» para la deuda de un médico que cobra menos de lo que debe.

Cuando los débitos de un médico en un pago superan lo que produjo, la diferencia no se
puede pagar ni se puede perder: el cierre del pago la deja como una deducción pendiente
que entra sola al próximo pago donde ese médico participe. Esas deducciones cuelgan de
este concepto, que es lo que permite reconocerlas (y borrarlas si se reabre el pago).

Aditivo e idempotente: inserta el concepto si no existe (nro_colegio 9900, sin precio ni
porcentaje).

Revision ID: saldodeu01
Revises: liqfact01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "saldodeu01"
down_revision: Union[str, None] = "liqfact01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NRO_COLEGIO = 9900
NOMBRE = "Saldo deudor de débitos"


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "INSERT INTO conceptos (nro_colegio, nombre, precio, porcentaje, aplica_a_todos) "
            "SELECT :nro, :nombre, 0, 0, 0 FROM DUAL "
            "WHERE NOT EXISTS (SELECT 1 FROM conceptos WHERE nro_colegio = :nro)"
        ),
        {"nro": NRO_COLEGIO, "nombre": NOMBRE},
    )


def downgrade() -> None:
    # Se borra sólo si ninguna deducción lo usa; si no, queda para no dejar deudas huérfanas.
    op.get_bind().execute(
        sa.text(
            "DELETE FROM conceptos WHERE nro_colegio = :nro "
            "AND NOT EXISTS (SELECT 1 FROM deducciones d WHERE d.descuento_id = conceptos.id)"
        ),
        {"nro": NRO_COLEGIO},
    )
