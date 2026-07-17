"""doble_periodo_medico_colegio

Soporte de doble período (médico / colegio) + ventana por obra social.

- `obras_sociales.dia_corte` (INT, default 20): ventana del período. 1 = mes completo,
  20 = del 20 al 20. Se marca la OS 151 (Poder Judicial) como mes completo (1).
- `facturacion.estado_doctor` (CHAR(1), default 'C'): fase médico de la cabecera
  (la fase colegio sigue en `estado`). Los históricos quedan en 'C' (ya cerrados).
- `detalle_facturacion.origen_carga` (VARCHAR(10), default 'colegio'): quién cargó la
  prestación (histórico → 'colegio').
- `periodo_medico_actual`: puntero del período abierto para médicos (global con
  override por OS). Reemplaza la tabla legacy `periodos_doctor` (deprecada). Se
  siembra la fila global con MAX(facturacion.periodo)+1.

Revision ID: d7e8f9a0b1c2
Revises: f3a4b5c6d7e8
Create Date: 2026-07-12 00:00:00.000000

"""
import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd7e8f9a0b1c2'
down_revision: Union[str, Sequence[str], None] = 'f3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _periodo_global_semilla(conn) -> str:
    """MAX(facturacion.periodo) + 1 mes; si no hay datos, el mes calendario actual."""
    ultimo = conn.execute(sa.text("SELECT MAX(periodo) FROM facturacion")).scalar()
    s = str(ultimo) if ultimo is not None else ""
    if len(s) == 6 and s.isdigit():
        total = int(s[:4]) * 12 + (int(s[4:6]) - 1) + 1
        y, m = divmod(total, 12)
        return f"{y}{m + 1:02d}"
    hoy = datetime.date.today()
    return f"{hoy.year}{hoy.month:02d}"


def upgrade() -> None:
    conn = op.get_bind()
    # Fechas '0000-00-00' legacy en facturacion/detalle rompen ALTER en modo estricto.
    op.execute("SET SESSION sql_mode=''")

    # ── Ventana por OS ────────────────────────────────────────────────────────
    op.add_column(
        "obras_sociales",
        sa.Column("dia_corte", sa.Integer(), nullable=False, server_default=sa.text("20")),
    )
    # Todas son del 20 al 20 MENOS Poder Judicial (OS 151) que es a mes completo.
    op.execute("UPDATE obras_sociales SET dia_corte = 1 WHERE NRO_OBRASOCIAL = 151")

    # ── Fase médico de la cabecera ────────────────────────────────────────────
    op.add_column(
        "facturacion",
        sa.Column("estado_doctor", sa.String(length=1), nullable=False,
                  server_default="C"),
    )

    # ── Origen de la prestación ───────────────────────────────────────────────
    op.add_column(
        "detalle_facturacion",
        sa.Column("origen_carga", sa.String(length=10), nullable=False,
                  server_default="colegio"),
    )

    # ── Puntero del período de médicos (global + override) ────────────────────
    op.create_table(
        "periodo_medico_actual",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("obra_social_id", sa.Integer(), nullable=True),
        sa.Column("periodo", sa.String(length=6), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_periodo_medico_actual_obra_social_id",
        "periodo_medico_actual", ["obra_social_id"],
    )
    # Semilla del período global.
    conn.execute(
        sa.text(
            "INSERT INTO periodo_medico_actual (obra_social_id, periodo) "
            "VALUES (NULL, :p)"
        ),
        {"p": _periodo_global_semilla(conn)},
    )


def downgrade() -> None:
    op.execute("SET SESSION sql_mode=''")
    op.drop_index("ix_periodo_medico_actual_obra_social_id", table_name="periodo_medico_actual")
    op.drop_table("periodo_medico_actual")
    op.drop_column("detalle_facturacion", "origen_carga")
    op.drop_column("facturacion", "estado_doctor")
    op.drop_column("obras_sociales", "dia_corte")
