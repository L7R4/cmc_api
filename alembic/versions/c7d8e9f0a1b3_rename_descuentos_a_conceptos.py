"""rename_descuentos_a_conceptos

Revision ID: c7d8e9f0a1b3
Revises: b6c1d3e9a072
Create Date: 2026-09-12

Renombra la tabla `descuentos` a `conceptos` (y su modelo ORM Descuentos ->
Conceptos, ver app/db/models/financiero.py). InnoDB actualiza automáticamente
las FK que apuntan a la tabla renombrada (socio_descuento.descuento_id,
deducciones.descuento_id) — no hace falta recrearlas.

También renombra los 4 códigos de permiso RBAC asociados (descuento:leer/
crear/editar/eliminar -> concepto:leer/crear/editar/eliminar) haciendo UPDATE
in-place sobre `permissions.code`: como role_permission/user_permission
enlazan por permission_id (no por code), esto no rompe ninguna asignación de
rol o de usuario existente.

Requiere deploy coordinado: el código nuevo (app/auth/scopes.py con
Scope.CONCEPTO_*, /api/conceptos) tiene que entrar en el mismo corte que esta
migración — mientras conviven versiones viejas del código con la tabla ya
renombrada (o viceversa), esos endpoints van a fallar.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c7d8e9f0a1b3'
down_revision = 'b6c1d3e9a072'
branch_labels = None
depends_on = None


#            old_code             new_code            old_desc                                        new_desc
_CODES = [
    ("descuento:leer",     "concepto:leer",     "Ver el catálogo de descuentos y sus socios",       "Ver el catálogo de conceptos y sus socios"),
    ("descuento:crear",    "concepto:crear",    "Crear descuentos y asignar socios",                "Crear conceptos y asignar socios"),
    ("descuento:editar",   "concepto:editar",   "Editar descuentos y sus asignaciones",              "Editar conceptos y sus asignaciones"),
    ("descuento:eliminar", "concepto:eliminar", "Eliminar descuentos y desasignar socios",           "Eliminar conceptos y desasignar socios"),
]


def upgrade() -> None:
    op.rename_table("descuentos", "conceptos")

    conn = op.get_bind()
    for old_code, new_code, old_desc, new_desc in _CODES:
        conn.execute(
            sa.text("UPDATE permissions SET code = :new_code, description = :new_desc WHERE code = :old_code"),
            {"new_code": new_code, "new_desc": new_desc, "old_code": old_code},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for old_code, new_code, old_desc, new_desc in _CODES:
        conn.execute(
            sa.text("UPDATE permissions SET code = :old_code, description = :old_desc WHERE code = :new_code"),
            {"old_code": old_code, "old_desc": old_desc, "new_code": new_code},
        )

    op.rename_table("conceptos", "descuentos")
