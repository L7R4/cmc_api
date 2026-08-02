"""Agregar tabla beneficios + permiso RBAC beneficios:gestionar

Beneficios/convenios para socios: los administra la web y los lee la app móvil
(GET /api/mobile/beneficios). Aditivo: no toca ninguna tabla existente.

Revision ID: t3u4v5w6x7y8
Revises: c9d0e1f2a3b4
Create Date: 2026-07-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "t3u4v5w6x7y8"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PERM_CODE = "beneficios:gestionar"
PERM_DESC = "Administrar beneficios y convenios para socios"


def upgrade() -> None:
    op.create_table(
        "beneficios",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("titulo", sa.String(160), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=False),
        sa.Column("descuento", sa.String(60), nullable=True),
        sa.Column("categoria", sa.String(40), nullable=False),
        sa.Column("ubicacion", sa.String(200), nullable=True),
        sa.Column("vigencia_hasta", sa.Date(), nullable=True),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("1")),
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
    )
    op.create_index(
        "ix_beneficios_activo_categoria", "beneficios", ["activo", "categoria"]
    )

    # ── RBAC: alta del permiso y asignación al rol admin ──────────────────────
    # Idempotente (INSERT ... WHERE NOT EXISTS) y tolerante a que no exista el
    # rol 'admin': en ese caso sólo queda el permiso, asignable desde
    # /api/admin/rbac/roles/{rol}/permissions/{code}.
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
    op.drop_index("ix_beneficios_activo_categoria", table_name="beneficios")
    op.drop_table("beneficios")
