"""Agregar tabla avisos_push + permiso RBAC avisos:gestionar

Avisos del Colegio para los socios: los publica el panel web y los lee la app
móvil (GET /api/mobile/avisos). Aditivo: no toca ninguna tabla existente.

OJO: la tabla se llama `avisos_push`, NO `avisos` — `avisos` ya existe en el
esquema legacy (ID/ARCHIVO/FECHA/EXISTE/AVISO, la usa el PHP viejo) y es otra
cosa. Ver app/db/models/avisos_push.py.

Revision ID: w6x7y8z9a0b1
Revises: v5w6x7y8z9a0
Create Date: 2026-07-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "w6x7y8z9a0b1"
down_revision: Union[str, None] = "v5w6x7y8z9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PERM_CODE = "avisos:gestionar"
PERM_DESC = "Publicar avisos y notificaciones para los socios de la app"


def upgrade() -> None:
    op.create_table(
        "avisos_push",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("titulo", sa.String(120), nullable=False),
        sa.Column("mensaje", sa.String(500), nullable=False),
        sa.Column(
            "tipo", sa.String(40), nullable=False, server_default="General"
        ),
        sa.Column(
            "publicado_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "push_estado", sa.String(20), nullable=False, server_default="pendiente"
        ),
        sa.Column("push_error", sa.String(255), nullable=True),
        sa.Column("destinatarios", sa.Integer(), nullable=True),
        sa.Column("enviado_por", sa.Integer(), nullable=True),
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
            ["enviado_por"],
            ["listado_medico.ID"],
            name="fk_avisos_push_enviado_por",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_avisos_push_activo_publicado", "avisos_push", ["activo", "publicado_at"]
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
    op.drop_index("ix_avisos_push_activo_publicado", table_name="avisos_push")
    op.drop_table("avisos_push")
