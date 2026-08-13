"""RBAC: permiso temporal panel:ingresar

Prueba controlada del panel nuevo con médicos seleccionados: quien tenga este
permiso se queda en `/panel/dashboard` después del login en vez de ir al
legacy (ver `src/app/pages/Login/Login.tsx`). No gatea ningún endpoint — la
autorización de las pantallas la sigue dando el rol `medico`.

No va en `role_permission`: se otorga nominalmente por usuario vía
`UserPermission.allow`, igual que los permisos de `CRITICOS`. Ver
`app/auth/scopes.py::Scope.PANEL_INGRESAR`.

Es temporal a propósito: cuando cierre la prueba, `downgrade()` borra tanto
los overrides que se hayan otorgado como el permiso en sí.

Revision ID: e4f5a6b7c8d9
Revises: c5d6e7f8a2b3
Create Date: 2026-08-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.auth.scopes import Scope

revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, Sequence[str], None] = "c5d6e7f8a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CODE = Scope.PANEL_INGRESAR.value
DESCRIPCION = "Ingreso al panel nuevo (prueba controlada, temporal)"


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "INSERT IGNORE INTO permissions (code, description) "
            "VALUES (:code, :desc)"
        ),
        {"code": CODE, "desc": DESCRIPCION},
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE up FROM user_permission up "
            "JOIN permissions p ON p.id = up.permission_id "
            "WHERE p.code = :code"
        ),
        {"code": CODE},
    )
    conn.execute(sa.text("DELETE FROM permissions WHERE code = :code"), {"code": CODE})
