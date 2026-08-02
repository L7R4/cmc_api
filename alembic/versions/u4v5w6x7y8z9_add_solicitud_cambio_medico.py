"""Agregar tabla solicitud_cambio_medico + permiso RBAC solicitudes_cambio:gestionar

Reclamos de corrección de datos que los médicos envían desde la app móvil
(POST /api/mobile/solicitudes-cambio) y que resuelve la web. Aditivo.

Revision ID: u4v5w6x7y8z9
Revises: t3u4v5w6x7y8
Create Date: 2026-07-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "u4v5w6x7y8z9"
down_revision: Union[str, None] = "t3u4v5w6x7y8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PERM_CODE = "solicitudes_cambio:gestionar"
PERM_DESC = "Gestionar solicitudes de cambio de datos enviadas por los médicos"


def upgrade() -> None:
    op.create_table(
        "solicitud_cambio_medico",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("nro_socio", sa.Integer(), nullable=False),
        sa.Column("medico_id", sa.Integer(), nullable=True),
        sa.Column("campo", sa.String(40), nullable=False),
        sa.Column("valor_actual", sa.String(255), nullable=True),
        sa.Column("valor_propuesto", sa.String(255), nullable=True),
        sa.Column("mensaje", sa.Text(), nullable=False),
        sa.Column(
            "estado",
            sa.Enum("pendiente", "aprobada", "rechazada", name="estado_solicitud_cambio"),
            nullable=False,
            server_default="pendiente",
        ),
        sa.Column("revisado_por", sa.Integer(), nullable=True),
        sa.Column("revisado_at", sa.DateTime(), nullable=True),
        sa.Column("respuesta_admin", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["medico_id"],
            ["listado_medico.ID"],
            name="fk_solicitud_cambio_medico",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_solicitud_cambio_medico_nro_socio", "solicitud_cambio_medico", ["nro_socio"])
    op.create_index("ix_solicitud_cambio_medico_medico_id", "solicitud_cambio_medico", ["medico_id"])
    op.create_index(
        "ix_solicitud_cambio_estado_created", "solicitud_cambio_medico", ["estado", "created_at"]
    )

    # ── RBAC: alta del permiso y asignación al rol admin (ver t3u4v5w6x7y8) ───
    op.execute(
        sa.text(
            "INSERT INTO permissions (code, description) "
            "SELECT :code, :descr FROM DUAL "
            "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :code)"
        ).bindparams(code=PERM_CODE, descr=PERM_DESC)
    )
    op.execute(
        sa.text(
            "INSERT INTO role_permission (role_id, permission_id) "
            "SELECT r.id, p.id FROM roles r JOIN permissions p ON p.code = :code "
            "WHERE r.name = 'admin' AND NOT EXISTS ("
            "  SELECT 1 FROM role_permission rp"
            "  WHERE rp.role_id = r.id AND rp.permission_id = p.id)"
        ).bindparams(code=PERM_CODE)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE rp FROM role_permission rp JOIN permissions p "
            "ON p.id = rp.permission_id WHERE p.code = :code"
        ).bindparams(code=PERM_CODE)
    )
    op.execute(sa.text("DELETE FROM permissions WHERE code = :code").bindparams(code=PERM_CODE))
    op.drop_index("ix_solicitud_cambio_estado_created", table_name="solicitud_cambio_medico")
    op.drop_index("ix_solicitud_cambio_medico_medico_id", table_name="solicitud_cambio_medico")
    op.drop_index("ix_solicitud_cambio_medico_nro_socio", table_name="solicitud_cambio_medico")
    op.drop_table("solicitud_cambio_medico")
