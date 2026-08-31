"""Datos del Colegio (institucion + contactos) y calendarios (agenda_eventos)

Cuatro tablas nuevas. **No toca nada existente**: ni columnas, ni permisos, ni
roles. Los endpoints nuevos se autorizan con `catalogo:leer` / `catalogo:editar`
y `rbac:gestionar`, que ya están en el catálogo, así que no hay nada que
sincronizar en `permissions` ni en `role_permission`.

  * `institucion` — una sola fila con CUIT, CBU, alias, domicilio.
  * `institucion_telefonos` — las líneas del Colegio.
  * `institucion_emails` — las casillas, con la contraseña **cifrada**
    (`app/core/secretos.py`). La llave vive en `SECRETOS_KEY`, fuera de la base:
    un dump de MySQL no alcanza para leerlas.
  * `agenda_eventos` — los tres calendarios (feriados, cumpleaños, tareas),
    discriminados por `tipo`.

El `downgrade` borra las cuatro. Es destructivo y no hay forma de que no lo sea:
son tablas nuevas, lo que tengan adentro se cargó después de esta revisión.

Revision ID: inst4g3nd001
Revises: 07026af30714
Create Date: 2026-08-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "inst4g3nd001"
down_revision: Union[str, Sequence[str], None] = "07026af30714"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "institucion",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("razon_social", sa.String(length=200), nullable=True),
        sa.Column("cuit", sa.String(length=13), nullable=True),
        sa.Column("condicion_iva", sa.String(length=60), nullable=True),
        sa.Column("ingresos_brutos", sa.String(length=40), nullable=True),
        sa.Column("cbu", sa.String(length=22), nullable=True),
        sa.Column("alias_cbu", sa.String(length=60), nullable=True),
        sa.Column("banco", sa.String(length=120), nullable=True),
        sa.Column("titular_cuenta", sa.String(length=200), nullable=True),
        sa.Column("domicilio", sa.String(length=200), nullable=True),
        sa.Column("localidad", sa.String(length=120), nullable=True),
        sa.Column("provincia", sa.String(length=120), nullable=True),
        sa.Column("codigo_postal", sa.String(length=20), nullable=True),
        sa.Column("sitio_web", sa.String(length=200), nullable=True),
        sa.Column("horario_atencion", sa.String(length=200), nullable=True),
        sa.Column("notas", sa.Text(), nullable=True),
        sa.Column(
            "actualizado_en",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column("actualizado_por", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )

    op.create_table(
        "institucion_telefonos",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("institucion_id", sa.Integer(), nullable=False),
        sa.Column("etiqueta", sa.String(length=80), nullable=True),
        sa.Column("numero", sa.String(length=60), nullable=False),
        sa.Column("notas", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["institucion_id"], ["institucion.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index(
        "ix_institucion_telefonos_institucion_id", "institucion_telefonos", ["institucion_id"]
    )

    op.create_table(
        "institucion_emails",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("institucion_id", sa.Integer(), nullable=False),
        sa.Column("etiqueta", sa.String(length=80), nullable=True),
        sa.Column("direccion", sa.String(length=200), nullable=False),
        sa.Column("servidor_entrante", sa.String(length=200), nullable=True),
        sa.Column("servidor_saliente", sa.String(length=200), nullable=True),
        # Token Fernet, no la contraseña. 512 deja lugar de sobra: una clave de
        # 64 caracteres da un token de ~180.
        sa.Column("password_cifrada", sa.String(length=512), nullable=True),
        sa.Column("password_actualizada_en", sa.DateTime(), nullable=True),
        sa.Column("notas", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["institucion_id"], ["institucion.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index(
        "ix_institucion_emails_institucion_id", "institucion_emails", ["institucion_id"]
    )

    op.create_table(
        "agenda_eventos",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "tipo",
            sa.Enum("feriado", "cumpleanos", "tarea", name="agenda_tipo_enum"),
            nullable=False,
        ),
        sa.Column("titulo", sa.String(length=200), nullable=False),
        sa.Column("descripcion", sa.String(length=500), nullable=True),
        sa.Column(
            "recurrencia",
            sa.Enum("unica", "anual", "mensual", name="agenda_recurrencia_enum"),
            server_default="unica",
            nullable=False,
        ),
        sa.Column("fecha", sa.Date(), nullable=True),
        sa.Column("dia", sa.SmallInteger(), nullable=True),
        sa.Column("mes", sa.SmallInteger(), nullable=True),
        sa.Column("medico_id", sa.Integer(), nullable=True),
        sa.Column("responsable", sa.String(length=120), nullable=True),
        sa.Column("color", sa.String(length=9), nullable=True),
        sa.Column("activo", sa.Boolean(), server_default="1", nullable=False),
        sa.Column(
            "creado_en", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True
        ),
        sa.Column(
            "actualizado_en",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column("actualizado_por", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_agenda_tipo_activo", "agenda_eventos", ["tipo", "activo"])
    op.create_index("ix_agenda_fecha", "agenda_eventos", ["fecha"])
    op.create_index("ix_agenda_mes_dia", "agenda_eventos", ["mes", "dia"])


def downgrade() -> None:
    op.drop_index("ix_agenda_mes_dia", table_name="agenda_eventos")
    op.drop_index("ix_agenda_fecha", table_name="agenda_eventos")
    op.drop_index("ix_agenda_tipo_activo", table_name="agenda_eventos")
    op.drop_table("agenda_eventos")

    op.drop_index("ix_institucion_emails_institucion_id", table_name="institucion_emails")
    op.drop_table("institucion_emails")
    op.drop_index("ix_institucion_telefonos_institucion_id", table_name="institucion_telefonos")
    op.drop_table("institucion_telefonos")
    op.drop_table("institucion")
