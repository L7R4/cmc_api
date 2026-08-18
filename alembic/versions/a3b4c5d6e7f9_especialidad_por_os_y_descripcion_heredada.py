"""especialidad_por_os_y_descripcion_heredada

Dos cambios que hacen que las tres capas del nomenclador obedezcan la MISMA regla:
"lo específico de la obra social gana sobre lo compartido; NULL = no opinó, hereda".

1. `nm_nomenclador_especialidad.obra_social_nro` (nullable) — la misma práctica puede
   estar clasificada en distinta especialidad según la OS (el 080801 es patología para
   una y oftalmología para otra) sin tener que duplicar la fila del catálogo. NULL =
   regla del Colegio, vale para todas; N = regla propia de esa obra social, que gana.
   La unique pasa a incluir `obra_social_key` para que convivan la compartida y las
   propias.

2. `nm_valores.descripcion` deja de ser una copia y pasa a heredar de verdad. Se
   escribía con avidez al crear el valor (`descripcion or nom.descripcion`), así que
   99,998% de las filas eran un duplicado exacto del catálogo: corregir una descripción
   en el catálogo no propagaba nunca. Se normaliza a NULL donde coincide; queda solo el
   override real.

Ninguno de los dos cambia comportamiento al aplicarse: no hay reglas por OS todavía, y
la descripción efectiva que se muestra es la misma (la resuelve
`service.descripcion_efectiva`).

⚠ MariaDB (producción) NO admite NOT NULL en columnas generadas — `obra_social_key` va
sin la restricción, igual que en `nm_nomenclador`. DDL para prod en
`docs/api/imports/2026-08-10_especialidad_por_os.sql`.

Revision ID: a3b4c5d6e7f9
Revises: f2a3b4c5d6e8
Create Date: 2026-08-10

"""
from alembic import op
import sqlalchemy as sa

revision = 'a3b4c5d6e7f9'
down_revision = 'f2a3b4c5d6e8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Especialidad por obra social ──────────────────────────────────────
    op.add_column(
        "nm_nomenclador_especialidad",
        sa.Column("obra_social_nro", sa.Integer(), nullable=True),
    )
    op.add_column(
        "nm_nomenclador_especialidad",
        sa.Column(
            "obra_social_key",
            sa.Integer(),
            sa.Computed("coalesce(obra_social_nro, -1)", persisted=True),
            nullable=True,  # MariaDB no admite NOT NULL en generadas; COALESCE nunca es NULL
        ),
    )
    # ORDEN IMPORTANTE: primero se crea la unique nueva y recién después se dropea la
    # vieja. `uq_nm_nom_esp` es el índice que sostiene la FK a nm_nomenclador
    # (`nomenclador_id` es su primera columna), así que dropearlo antes falla con
    # "Cannot drop index ... needed in a foreign key constraint". La nueva también
    # arranca con `nomenclador_id`, así que puede cubrir la FK.
    op.create_unique_constraint(
        "uq_nm_nom_esp_os",
        "nm_nomenclador_especialidad",
        ["nomenclador_id", "especialidad_id_colegio", "obra_social_key"],
    )
    op.drop_constraint("uq_nm_nom_esp", "nm_nomenclador_especialidad", type_="unique")
    op.create_index(
        "ix_nm_nom_esp_os", "nm_nomenclador_especialidad", ["obra_social_nro"]
    )

    # ── 2. La descripción del valor pasa a heredar de verdad ─────────────────
    op.execute(
        """
        UPDATE nm_valores v
          JOIN nm_nomenclador n ON n.id = v.nomenclador_id
           SET v.descripcion = NULL
         WHERE v.descripcion IS NOT NULL
           AND v.descripcion = n.descripcion
        """
    )


def downgrade() -> None:
    # La descripción no se "des-normaliza": se rellena desde el catálogo, que es
    # exactamente lo que la copia con avidez hacía.
    op.execute(
        """
        UPDATE nm_valores v
          JOIN nm_nomenclador n ON n.id = v.nomenclador_id
           SET v.descripcion = n.descripcion
         WHERE v.descripcion IS NULL
        """
    )
    op.drop_index("ix_nm_nom_esp_os", "nm_nomenclador_especialidad")
    # Mismo cuidado que en upgrade: se crea la unique vieja antes de dropear la nueva,
    # porque una de las dos tiene que estar cubriendo la FK todo el tiempo.
    # Falla a propósito si ya hay reglas propias de alguna OS: volver a la unique sin
    # el eje significaría perder filas.
    op.create_unique_constraint(
        "uq_nm_nom_esp",
        "nm_nomenclador_especialidad",
        ["nomenclador_id", "especialidad_id_colegio"],
    )
    op.drop_constraint("uq_nm_nom_esp_os", "nm_nomenclador_especialidad", type_="unique")
    op.drop_column("nm_nomenclador_especialidad", "obra_social_key")
    op.drop_column("nm_nomenclador_especialidad", "obra_social_nro")
