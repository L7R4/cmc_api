"""RBAC: alta de cobranza:leer (panel de Cobranzas por concepto)

Agrega el permiso `Scope.COBRANZA_LEER` al catálogo y lo asigna a los roles
que lo llevan en `ROLES` (`app/auth/scopes.py`): `admin` (lo hereda por
`set(Scope) - {...}`) y `liquidador` (agregado explícito). Sigue el patrón de
`f5a6b7c8d9e0_rbac_sync_catalogo.py`: INSERT IGNORE idempotente en
`permissions`, luego INSERT IGNORE ... SELECT en `role_permission`.

Revision ID: c0br4nz4s001
Revises: c4nt1d4dw1d3
Create Date: 2026-08-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.auth.scopes import DESCRIPCIONES, Scope

revision: str = "c0br4nz4s001"
down_revision: Union[str, Sequence[str], None] = "c4nt1d4dw1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ROLES_CON_EL_PERMISO = ("admin", "liquidador")


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        sa.text(
            "INSERT IGNORE INTO permissions (code, description) "
            "VALUES (:code, :desc)"
        ),
        {"code": Scope.COBRANZA_LEER.value, "desc": DESCRIPCIONES[Scope.COBRANZA_LEER]},
    )

    for rol in ROLES_CON_EL_PERMISO:
        conn.execute(
            sa.text(
                "INSERT IGNORE INTO role_permission (role_id, permission_id) "
                "SELECT r.id, p.id FROM roles r, permissions p "
                "WHERE r.name = :rol AND p.code = :code"
            ),
            {"rol": rol, "code": Scope.COBRANZA_LEER.value},
        )


def downgrade() -> None:
    conn = op.get_bind()

    for rol in ROLES_CON_EL_PERMISO:
        conn.execute(
            sa.text(
                "DELETE rp FROM role_permission rp "
                "JOIN roles r ON r.id = rp.role_id "
                "JOIN permissions p ON p.id = rp.permission_id "
                "WHERE r.name = :rol AND p.code = :code"
            ),
            {"rol": rol, "code": Scope.COBRANZA_LEER.value},
        )

    conn.execute(
        sa.text("DELETE FROM permissions WHERE code = :code"),
        {"code": Scope.COBRANZA_LEER.value},
    )
