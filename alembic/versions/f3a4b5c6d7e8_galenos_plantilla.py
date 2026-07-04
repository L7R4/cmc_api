"""galenos_plantilla

Crea `nm_galenos_plantilla`: plantillas prearmadas de galenos, cargadas a mano por
el programador (sin endpoints de escritura ni acceso de usuarios). Un `grupo` agrupa
las filas que componen un galeno completo listo para instanciar (una fila por nivel,
o una sola con nivel NULL si no es nivelado). El front las consulta (GET) para
pre-armar el formulario de POST /galenos/crear_niveles.

Sin obra_social_nro (genéricas, reutilizables para cualquier OS) y sin
vigencia/activo/observacion (no aplican a una plantilla). `valor_unitario` se
seedea en 0 (informativo). `codigo` es el slug real que tendrá el galeno en
nm_galenos al instanciarse (debe coincidir con slugify_codigo(nombre)).

Revision ID: f3a4b5c6d7e8
Revises: c0d1e2f3a4b5
Create Date: 2026-07-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, Sequence[str], None] = 'c0d1e2f3a4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "nm_galenos_plantilla",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("grupo", sa.String(100), nullable=False),
        sa.Column("codigo", sa.String(100), nullable=False),
        sa.Column("nombre", sa.String(200), nullable=False),
        sa.Column("nivel", sa.Integer(), nullable=True),
        sa.Column(
            "nivel_key",
            sa.Integer(),
            sa.Computed("coalesce(nivel, -1)", persisted=True),
            nullable=False,
        ),
        sa.Column("valor_unitario", sa.DECIMAL(14, 2), nullable=False),
        sa.Column("unidades_honorarios", sa.DECIMAL(10, 2), nullable=True),
        sa.Column("unidades_ayudante", sa.DECIMAL(10, 2), nullable=True),
        sa.Column("unidades_gastos", sa.DECIMAL(10, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()"),
                  onupdate=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("grupo", "nivel_key", name="uq_nm_galenos_plantilla_grupo_nivel"),
    )
    op.create_index("ix_nm_galenos_plantilla_grupo", "nm_galenos_plantilla", ["grupo"])


def downgrade() -> None:
    op.drop_index("ix_nm_galenos_plantilla_grupo", table_name="nm_galenos_plantilla")
    op.drop_table("nm_galenos_plantilla")
