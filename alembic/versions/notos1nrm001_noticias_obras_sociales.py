"""noticias_obras_sociales — normas operativas por obra social

Una tabla nueva de dos columnas. No toca `noticias` ni ninguna tabla existente,
no agrega permisos ni roles: la lectura usa `catalogo:leer` y la escritura
`contenido:editar`, que ya están en el catálogo.

Por qué una tabla y no una columna en `noticias`: una misma norma alcanza a
varias obras sociales, y guardarla en el `badge` (texto libre de 80 caracteres)
obligaría a parsear texto para saber a qué obra social pertenece. El badge
decide cómo se muestra la noticia; esta tabla decide a quién le corresponde.

`nro_obrasocial` va sin foreign key, igual que `boletin_observacion`:
`obras_sociales.NRO_OBRASOCIAL` está indexada pero no es única, y MySQL no
admite FK contra una columna no única. El número se valida en el handler.

Revision ID: notos1nrm001
Revises: vigos1dx0001
Create Date: 2026-08-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "notos1nrm001"
down_revision: Union[str, Sequence[str], None] = "vigos1dx0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "noticias_obras_sociales",
        sa.Column("noticia_id", sa.Integer(), nullable=False),
        sa.Column("nro_obrasocial", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["noticia_id"], ["noticias.id"], ondelete="CASCADE"),
        # PK compuesta: además de identificar la fila, impide asociar dos veces
        # la misma obra social a la misma noticia.
        sa.PrimaryKeyConstraint("noticia_id", "nro_obrasocial"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    # El boletín entra siempre por obra social ("¿qué normas tiene la 256?"), y
    # la PK compuesta arranca por noticia_id, así que no sirve para ese acceso.
    # Para noticia_id no hace falta índice: MySQL crea uno solo con la FK.
    op.create_index(
        "ix_noticias_os_nro", "noticias_obras_sociales", ["nro_obrasocial"]
    )


def downgrade() -> None:
    op.drop_index("ix_noticias_os_nro", table_name="noticias_obras_sociales")
    op.drop_table("noticias_obras_sociales")
