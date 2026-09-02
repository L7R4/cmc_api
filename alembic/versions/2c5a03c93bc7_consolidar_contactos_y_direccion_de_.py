"""consolidar contactos y direccion de obras_sociales en JSON, ampliar nombre y archivo, UNIQUE en nro_obrasocial

Tres cambios independientes que salen de la auditoría de `/panel/convenios`:

  * **O-05** — `obras_sociales_contactos` y `obras_sociales_direccion` tenían
    0 filas en las 141 obras sociales reales. Sin un solo registro cargado no
    hay caso de uso que justifique el join: acá se consulta siempre "todos los
    contactos de esta obra social", nunca un contacto individual. Se
    reemplazan por dos columnas JSON en `obras_sociales`, con el mismo patrón
    que ya usa `listado_medico.conceps_espec`. Ambas tablas están vacías, así
    que no hay filas que migrar.
  * **O-06** — `NRO_OBRASOCIAL` sólo era único por el SELECT previo del
    endpoint de alta; sin restricción real, dos altas simultáneas con el mismo
    número pasaban las dos. Se agrega la constraint en la base. Verificado
    antes de escribir esto que las 141 filas actuales no tienen duplicados.
  * **P-07 / O-10** — `avisos.ARCHIVO` (50) y `obras_sociales.OBRA_SOCIAL` (45)
    truncaban en silencio: un nombre de PDF o de obra social más largo que el
    límite se cortaba sin avisar. Se amplían a 255.

Revision ID: 2c5a03c93bc7
Revises: ad1173c60c63
Create Date: 2026-09-02 14:53:01.368329

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '2c5a03c93bc7'
down_revision: Union[str, Sequence[str], None] = 'ad1173c60c63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ── O-05: contactos y dirección → JSON en la fila ───────────────────────
    # MySQL 5.7 no admite DEFAULT en columnas JSON (error 1101): se agregan
    # nullable, se backfillea `[]` en las 141 filas existentes y recién ahí se
    # cierra el NOT NULL.
    op.add_column("obras_sociales", sa.Column("contactos", sa.JSON(), nullable=True))
    op.add_column("obras_sociales", sa.Column("direcciones", sa.JSON(), nullable=True))
    op.execute("UPDATE obras_sociales SET contactos = JSON_ARRAY() WHERE contactos IS NULL")
    op.execute("UPDATE obras_sociales SET direcciones = JSON_ARRAY() WHERE direcciones IS NULL")
    op.alter_column("obras_sociales", "contactos", existing_type=sa.JSON(), nullable=False)
    op.alter_column("obras_sociales", "direcciones", existing_type=sa.JSON(), nullable=False)

    op.drop_table("obras_sociales_contactos")
    op.drop_table("obras_sociales_direccion")

    # ── O-06: UNIQUE real sobre NRO_OBRASOCIAL ──────────────────────────────
    # `ajuste` y `lote_ajuste` tienen FK contra esta columna, y esa FK necesita
    # que exista SIEMPRE algún índice sobre ella — por eso la unique constraint
    # se crea primero (provee su propio índice) y el índice viejo, ahora
    # redundante, se borra después.
    op.create_unique_constraint(
        "uq_obras_sociales_nro", "obras_sociales", ["NRO_OBRASOCIAL"]
    )
    op.drop_index("NRO_OBRASOCIAL", table_name="obras_sociales")

    # ── P-07 / O-10: columnas que truncaban en silencio ─────────────────────
    op.alter_column(
        "avisos", "ARCHIVO",
        existing_type=mysql.VARCHAR(50, collation="utf8_spanish2_ci"),
        type_=mysql.VARCHAR(255, collation="utf8_spanish2_ci"),
        existing_nullable=False,
        existing_server_default=sa.text("'#'"),
    )
    op.alter_column(
        "obras_sociales", "OBRA_SOCIAL",
        existing_type=mysql.VARCHAR(45, collation="utf8_spanish2_ci"),
        type_=mysql.VARCHAR(255, collation="utf8_spanish2_ci"),
        existing_nullable=False,
        existing_server_default=sa.text("'a'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "obras_sociales", "OBRA_SOCIAL",
        existing_type=mysql.VARCHAR(255, collation="utf8_spanish2_ci"),
        type_=mysql.VARCHAR(45, collation="utf8_spanish2_ci"),
        existing_nullable=False,
        existing_server_default=sa.text("'a'"),
    )
    op.alter_column(
        "avisos", "ARCHIVO",
        existing_type=mysql.VARCHAR(255, collation="utf8_spanish2_ci"),
        type_=mysql.VARCHAR(50, collation="utf8_spanish2_ci"),
        existing_nullable=False,
        existing_server_default=sa.text("'#'"),
    )

    # Mismo orden invertido que en upgrade(): el índice nuevo tiene que existir
    # antes de soltar la constraint, porque las FK de `ajuste`/`lote_ajuste`
    # necesitan un índice sobre la columna en todo momento.
    op.create_index("NRO_OBRASOCIAL", "obras_sociales", ["NRO_OBRASOCIAL"])
    op.drop_constraint("uq_obras_sociales_nro", "obras_sociales", type_="unique")

    op.create_table(
        "obras_sociales_direccion",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("obra_social_id", sa.Integer(), nullable=False),
        sa.Column("provincia", sa.String(100), nullable=True),
        sa.Column("localidad", sa.String(100), nullable=True),
        sa.Column("direccion", sa.String(200), nullable=True),
        sa.Column("codigo_postal", sa.String(10), nullable=True),
        sa.Column("horario", sa.String(150), nullable=True),
        sa.ForeignKeyConstraint(["obra_social_id"], ["obras_sociales.ID"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_obras_sociales_direccion_obra_social_id"),
        "obras_sociales_direccion", ["obra_social_id"],
    )
    op.create_table(
        "obras_sociales_contactos",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("obra_social_id", sa.Integer(), nullable=False),
        sa.Column("tipo", sa.Enum("email", "telefono", name="contacto_tipo_enum"), nullable=False),
        sa.Column("valor", sa.String(200), nullable=False),
        sa.Column("etiqueta", sa.String(80), nullable=True),
        sa.ForeignKeyConstraint(["obra_social_id"], ["obras_sociales.ID"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_obras_sociales_contactos_obra_social_id"),
        "obras_sociales_contactos", ["obra_social_id"],
    )

    op.drop_column("obras_sociales", "direcciones")
    op.drop_column("obras_sociales", "contactos")
