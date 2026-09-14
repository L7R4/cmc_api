"""RBAC: alta de pago:marcar_pagado

Agrega el permiso `Scope.PAGO_MARCAR_PAGADO` al catálogo y lo asigna a los
roles que lo llevan en `ROLES` (`app/auth/scopes.py`): `admin` (lo hereda por
`set(Scope) - {...}`) y `liquidador` (agregado explícito). Sigue el patrón de
`c0br4nz4s001_add_cobranza_leer.py`: INSERT IGNORE idempotente en
`permissions`, luego INSERT IGNORE ... SELECT en `role_permission`.

Revision ID: a7b8c9d5e6f7
Revises: f6a7b8c9d5e6
Create Date: 2026-09-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.auth.scopes import DESCRIPCIONES, Scope

revision: str = "a7b8c9d5e6f7"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d5e6"
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
        {"code": Scope.PAGO_MARCAR_PAGADO.value, "desc": DESCRIPCIONES[Scope.PAGO_MARCAR_PAGADO]},
    )

    for rol in ROLES_CON_EL_PERMISO:
        conn.execute(
            sa.text(
                "INSERT IGNORE INTO role_permission (role_id, permission_id) "
                "SELECT r.id, p.id FROM roles r, permissions p "
                "WHERE r.name = :rol AND p.code = :code"
            ),
            {"rol": rol, "code": Scope.PAGO_MARCAR_PAGADO.value},
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
            {"rol": rol, "code": Scope.PAGO_MARCAR_PAGADO.value},
        )

    conn.execute(
        sa.text("DELETE FROM permissions WHERE code = :code"),
        {"code": Scope.PAGO_MARCAR_PAGADO.value},
    )
