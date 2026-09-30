"""nm_nomenclador: agrega descripcion default (opcional) + backfill desde NN

Reagrega `descripcion` a `nm_nomenclador` — la fase 3 de la reestructura del
nomenclador la había borrado porque dejó de ser la fuente de verdad de la
descripción (eso es `Valor.descripcion`, por obra social). Esta vuelve con otro
propósito: un default a nivel catálogo, nullable, que la API ofrece para
precargar la descripción al crear un Valor nuevo para un código que todavía no
tiene la suya en esa OS.

Backfill: para cada `nm_nomenclador` vinculado a un `nm_nomenclador_nacional`
(`nomenclador_nacional_id`), copia la descripción de ese NN. Va por la FK y no
por comparar `codigo` como string: los dos catálogos no siempre coinciden en
formato (ej. NN "000002" vs Colegio "002" — ver plan de migración, obstáculo
5), y la FK ya resuelve esa diferencia desde el backfill de la fase 1.

Revision ID: nn4nomdesc1
Revises: nn3fase3drop1
Create Date: 2026-09-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "nn4nomdesc1"
down_revision: Union[str, Sequence[str], None] = "nn3fase3drop1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("nm_nomenclador", sa.Column("descripcion", sa.String(255), nullable=True))
    op.execute(
        """
        UPDATE nm_nomenclador n
        JOIN nm_nomenclador_nacional nn ON nn.id = n.nomenclador_nacional_id
        SET n.descripcion = nn.descripcion
        WHERE nn.descripcion IS NOT NULL AND nn.descripcion <> ''
        """
    )


def downgrade() -> None:
    op.drop_column("nm_nomenclador", "descripcion")
