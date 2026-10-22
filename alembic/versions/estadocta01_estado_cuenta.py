"""Estado de la cuenta del socio: `listado_medico.estado_cuenta`

Reemplaza a `EXISTE`, que venía del sistema viejo y mezclaba dos cosas en la
misma letra: `'N'` era tanto "pendiente de aprobación" como "dado de baja".

* `estado_cuenta`: `pendiente` / `activo` / `baja`.
* `estado_cuenta_at`: cuándo cambió por última vez (NULL en las filas de la carga inicial).

Carga inicial, en este orden:

1. `EXISTE='S'` → `activo`.
2. Con una `solicitudes_registros` pendiente → `pendiente`.
3. Borradores del alta administrativa (`save_medico_admin_draft`): sin N° de socio,
   sin rol y sin ninguna solicitud → `pendiente`.
4. El resto → `baja` (socios dados de baja, solicitudes rechazadas y los legajos con
   N° de socio que no tienen rol ni solicitud).

`EXISTE` queda en la tabla sin que nada lo lea ni lo escriba. El `downgrade` borra las
dos columnas; lo cambiado después de esta revisión se pierde.

Revision ID: estadocta01
Revises: lotefact01
Create Date: 2026-10-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "estadocta01"
down_revision: Union[str, Sequence[str], None] = "lotefact01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tiene_columna(bind, tabla: str, columna: str) -> bool:
    return columna in {c["name"] for c in sa.inspect(bind).get_columns(tabla)}


def upgrade() -> None:
    bind = op.get_bind()
    if _tiene_columna(bind, "listado_medico", "estado_cuenta"):
        return

    op.add_column(
        "listado_medico",
        sa.Column(
            "estado_cuenta",
            sa.Enum("pendiente", "activo", "baja", name="estado_cuenta_enum"),
            nullable=False,
            server_default="pendiente",
        ),
    )
    op.add_column("listado_medico", sa.Column("estado_cuenta_at", sa.DateTime(), nullable=True))
    op.create_index("ix_listado_medico_estado_cuenta", "listado_medico", ["estado_cuenta"])

    op.execute(
        """
        UPDATE listado_medico m
        SET m.estado_cuenta = CASE
            WHEN m.EXISTE = 'S' THEN 'activo'
            WHEN EXISTS (
                SELECT 1 FROM solicitudes_registros s
                WHERE s.medico_id = m.ID AND s.estado = 'pendiente'
            ) THEN 'pendiente'
            WHEN (m.NRO_SOCIO IS NULL OR m.NRO_SOCIO = 0)
                 AND NOT EXISTS (SELECT 1 FROM user_role ur WHERE ur.user_id = m.ID)
                 AND NOT EXISTS (SELECT 1 FROM solicitudes_registros s WHERE s.medico_id = m.ID)
            THEN 'pendiente'
            ELSE 'baja'
        END
        """
    )


def downgrade() -> None:
    op.drop_index("ix_listado_medico_estado_cuenta", table_name="listado_medico")
    op.drop_column("listado_medico", "estado_cuenta_at")
    op.drop_column("listado_medico", "estado_cuenta")
