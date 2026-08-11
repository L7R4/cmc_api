"""requiere_autorizacion_por_codigo

Marca si una práctica necesita autorización previa de la obra social.

Mismo patrón de dos niveles que `categoria` y `complejidad`:
- `nm_nomenclador.requiere_autorizacion` — default del Colegio para el código (NOT NULL,
  arranca en 0: nadie requiere autorización hasta que alguien lo marque).
- `nm_valores.requiere_autorizacion` — override de esa obra social. **NULL = hereda** del
  catálogo, por eso es nullable y no lleva default.

Resolución: `service.requiere_autorizacion_efectiva(valor, nomenclador)`.

No confundir con `ObraManual.requiere_autorizacion`
(`app/modules/validaciones/service.py`), que es un "todo o nada" por obra social para las
dos OS de carga manual. Los dos niveles conviven con OR: se exige autorización si la OS la
exige para todo O el código la exige puntualmente.

⚠ Producción es MariaDB y no tiene `alembic_version` — DDL equivalente en
`docs/api/imports/2026-08-10_requiere_autorizacion_por_codigo.sql`.

Revision ID: f2a3b4c5d6e8
Revises: e1f2a3b4c5d6
Create Date: 2026-08-10

"""
from alembic import op
import sqlalchemy as sa

revision = 'f2a3b4c5d6e8'
down_revision = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "nm_nomenclador",
        sa.Column(
            "requiere_autorizacion",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )
    # Nullable a propósito: NULL = hereda del catálogo. Un default acá rompería la
    # herencia (no se distinguiría "esta OS dice que no" de "esta OS no opinó").
    op.add_column(
        "nm_valores",
        sa.Column("requiere_autorizacion", sa.Boolean(), nullable=True),
    )
    op.create_index(
        "ix_nm_nomenclador_requiere_autorizacion",
        "nm_nomenclador",
        ["requiere_autorizacion"],
    )


def downgrade() -> None:
    op.drop_index("ix_nm_nomenclador_requiere_autorizacion", "nm_nomenclador")
    op.drop_column("nm_valores", "requiere_autorizacion")
    op.drop_column("nm_nomenclador", "requiere_autorizacion")
