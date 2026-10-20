"""Documentación digital de las prestaciones

* `obras_sociales.documentacion_digital`: la obra social trabaja con documentación digital.
* `nm_codigo_obra_social.requiere_documentacion`: el código la exige en esa obra social.
* `detalle_facturacion_documento`: los archivos de cada prestación (varios, ordenados).
  Se llena además con el `orden_path` que ya existía: cada uno pasa a ser un documento
  `origen='medico'` (la ruta es la misma, así que el acceso no cambia).

`orden_path` se deja como está para no romper lecturas viejas, pero deja de escribirse.

El `downgrade` borra la tabla de documentos y las dos columnas. Es destructivo para lo que
se haya cargado después de esta revisión.

Revision ID: docdig001
Revises: tareasdia001
Create Date: 2026-10-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "docdig001"
down_revision: Union[str, Sequence[str], None] = "tareasdia001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tiene_columna(bind, tabla: str, columna: str) -> bool:
    return columna in {c["name"] for c in sa.inspect(bind).get_columns(tabla)}


def upgrade() -> None:
    bind = op.get_bind()
    # `obras_sociales.condicion_iva` tiene un valor repetido en su ENUM: en modo estricto
    # (MySQL 5.7) reconstruir la tabla falla con el error 1291.
    op.execute("SET SESSION sql_mode = ''")

    if not _tiene_columna(bind, "obras_sociales", "documentacion_digital"):
        op.add_column(
            "obras_sociales",
            sa.Column("documentacion_digital", sa.Boolean(), nullable=False, server_default="0"),
        )
    if not _tiene_columna(bind, "nm_codigo_obra_social", "requiere_documentacion"):
        op.add_column(
            "nm_codigo_obra_social",
            sa.Column("requiere_documentacion", sa.Boolean(), nullable=False, server_default="0"),
        )

    if "detalle_facturacion_documento" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "detalle_facturacion_documento",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "detalle_id",
                sa.BigInteger(),
                sa.ForeignKey("detalle_facturacion.id_detalle_prestaciones", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("posicion", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("path", sa.String(300), nullable=False),
            sa.Column("nombre_original", sa.String(255), nullable=False),
            sa.Column("content_type", sa.String(100), nullable=False),
            sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "origen",
                sa.Enum("medico", "colegio", name="detalle_doc_origen_enum"),
                nullable=False,
                server_default="medico",
            ),
            sa.Column("subido_por", sa.String(30), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )
        op.create_index("ix_detalle_doc_detalle", "detalle_facturacion_documento", ["detalle_id"])

        # Las órdenes que ya estaban adjuntas pasan a ser el primer documento de su prestación.
        op.execute(
            """
            INSERT INTO detalle_facturacion_documento
                (detalle_id, posicion, path, nombre_original, content_type, size, origen, subido_por)
            SELECT d.id_detalle_prestaciones, 0, d.orden_path,
                   SUBSTRING_INDEX(REPLACE(d.orden_path, CHAR(92), '/'), '/', -1),
                   'application/pdf', 0, 'medico', d.cod_med
              FROM detalle_facturacion d
             WHERE d.orden_path IS NOT NULL AND d.orden_path <> ''
            """
        )


def downgrade() -> None:
    op.drop_index("ix_detalle_doc_detalle", table_name="detalle_facturacion_documento")
    op.drop_table("detalle_facturacion_documento")
    op.drop_column("nm_codigo_obra_social", "requiere_documentacion")
    op.drop_column("obras_sociales", "documentacion_digital")
