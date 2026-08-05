"""RBAC: catálogo nuevo de permisos, roles, fix A14 y backfill A15

Implementa la Etapa 0 de `docs/api/RBAC_PROPUESTA.md` §7. Es **aditiva**: no
borra ninguno de los 30 permisos viejos ni sus asignaciones, para que los tokens
en circulación sigan funcionando mientras `RBAC_ENFORCE=False`. La limpieza es la
Etapa 3, en una migración aparte.

Lo que hace, en orden:

  1. Inserta los 57 permisos del catálogo de `app/auth/scopes.py`.
  2. Crea el rol `editor_web`.
  3. Arma `role_permission` según `ROLES`, salvo los 4 permisos de `CRITICOS`,
     que se otorgan nominalmente por usuario.
  4. **Quita `rbac:gestionar` del rol `contador`** — hallazgo A14 de la
     auditoría: era escalada a administrador total disponible en producción.
  5. **Backfill de roles** — hallazgo A15: los médicos activos sin ningún rol
     reciben `medico`. Sin esto, `get_current_user_with_scopes_and_role`
     responde 409 y el app móvil queda inutilizable para ellos.

Revision ID: z9a0b1c2d3e4
Revises: cf3a91d0e7b2, y8z9a0b1c2d3
Create Date: 2026-08-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.auth.scopes import CRITICOS, DESCRIPCIONES, ROLES, Scope

revision: str = "z9a0b1c2d3e4"
# Mergea las dos cabezas que la base de desarrollo tiene aplicadas.
down_revision: Union[str, Sequence[str], None] = ("cf3a91d0e7b2", "y8z9a0b1c2d3")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DESCRIPCION_ROLES = {
    "medico": "Prestador colegiado — opera sobre sus propios datos",
    "facturador": "Carga administrativa de prestaciones y padrón",
    "liquidador": "Arma liquidaciones y lotes; no sella ni revierte",
    "contador": "Control financiero: pagos, recibos y deducciones",
    "editor_web": "Contenido del portal: noticias, publicidad, avisos y beneficios",
    "admin": "Administración total",
}


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Permisos ──────────────────────────────────────────────────────────
    # INSERT IGNORE + UPDATE de description: idempotente y no pisa ids
    # existentes (`rbac:gestionar` y `auditoria:purgar` ya están y se conservan
    # con su id, para no romper las filas de role_permission que los referencian).
    for scope in Scope:
        conn.execute(
            sa.text(
                "INSERT IGNORE INTO permissions (code, description) "
                "VALUES (:code, :desc)"
            ),
            {"code": scope.value, "desc": DESCRIPCIONES[scope]},
        )
        conn.execute(
            sa.text("UPDATE permissions SET description = :desc WHERE code = :code"),
            {"code": scope.value, "desc": DESCRIPCIONES[scope]},
        )

    # ── 2. Roles ─────────────────────────────────────────────────────────────
    for nombre, desc in DESCRIPCION_ROLES.items():
        conn.execute(
            sa.text("INSERT IGNORE INTO roles (name, description) VALUES (:n, :d)"),
            {"n": nombre, "d": desc},
        )

    # ── 3. role_permission ───────────────────────────────────────────────────
    # Los CRITICOS se excluyen a propósito: van por UserPermission.allow para
    # que quede constancia nominal de quién puede hacer las 4 cosas más
    # peligrosas del sistema. Ver app/auth/scopes.py::CRITICOS.
    for rol, scopes in ROLES.items():
        for scope in sorted(scopes - CRITICOS):
            conn.execute(
                sa.text(
                    "INSERT IGNORE INTO role_permission (role_id, permission_id) "
                    "SELECT r.id, p.id FROM roles r, permissions p "
                    "WHERE r.name = :rol AND p.code = :code"
                ),
                {"rol": rol, "code": scope.value},
            )

    # ── 4. A14: quitar rbac:gestionar del rol contador ───────────────────────
    # Cualquier usuario con rol `contador` podía otorgarse a sí mismo — o a
    # otro — cualquier permiso del sistema, de forma persistente.
    conn.execute(
        sa.text(
            "DELETE rp FROM role_permission rp "
            "JOIN roles r ON r.id = rp.role_id "
            "JOIN permissions p ON p.id = rp.permission_id "
            "WHERE r.name = 'contador' AND p.code = 'rbac:gestionar'"
        )
    )

    # ── 5. A15: backfill de roles ────────────────────────────────────────────
    # Solo los activos (EXISTE='S'): dar rol a bajas y rechazados sería
    # ampliar el acceso, no restaurarlo.
    conn.execute(
        sa.text(
            "INSERT IGNORE INTO user_role (user_id, role_id) "
            "SELECT lm.ID, r.id "
            "FROM listado_medico lm "
            "CROSS JOIN roles r "
            "WHERE r.name = 'medico' "
            "  AND lm.EXISTE = 'S' "
            "  AND NOT EXISTS (SELECT 1 FROM user_role ur WHERE ur.user_id = lm.ID)"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()

    codigos = [s.value for s in Scope]
    # `rbac:gestionar` y `auditoria:purgar` son preexistentes: se dejan.
    nuevos = [c for c in codigos if c not in ("rbac:gestionar", "auditoria:purgar")]

    conn.execute(
        sa.text(
            "DELETE rp FROM role_permission rp "
            "JOIN permissions p ON p.id = rp.permission_id "
            "WHERE p.code IN :codes"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": nuevos},
    )
    conn.execute(
        sa.text("DELETE FROM permissions WHERE code IN :codes").bindparams(
            sa.bindparam("codes", expanding=True)
        ),
        {"codes": nuevos},
    )
    conn.execute(sa.text("DELETE FROM roles WHERE name = 'editor_web'"))

    # A propósito NO se revierten dos cosas:
    #   * el backfill de roles (A15) — quitarle el rol a 2.100 médicos los
    #     dejaría con 409 en todas las pantallas del app;
    #   * `rbac:gestionar` en `contador` (A14) — restaurar una escalada de
    #     privilegio en un downgrade sería reintroducir la vulnerabilidad.
