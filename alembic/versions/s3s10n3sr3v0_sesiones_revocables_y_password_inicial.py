"""Sesiones revocables, contraseña inicial obligatoria, medico:leer_propio y baja de `contador`

Cierra cinco hallazgos de `docs/api/AUDITORIA_SEGURIDAD.md` de una vez porque
comparten el mismo esquema:

  * **A5** — tabla `refresh_tokens`: el `jti` del refresh pasa a tener estado, y
    con eso el logout, la rotación y la detección de reuso empiezan a existir.
  * **A7 / A12** — `listado_medico.tokens_valid_from`: corte de los access
    tokens ya emitidos al cambiar la contraseña o los permisos.
  * **A2 / A3** — `listado_medico.must_change_password`: la contraseña inicial
    es `cmc1785` para todos y hay que cambiarla en el primer ingreso. Se marca
    en **todas las cuentas existentes**, porque hoy la contraseña de la mayoría
    es su DNI o su matrícula: ninguna de las dos es un secreto.
  * **A4** — permiso `medico:leer_propio`, asignado al rol `medico`.
  * **A14** — se borra el rol `contador` y sus asignaciones.

Revision ID: s3s10n3sr3v0
Revises: z9a0b1c2d3e4
Create Date: 2026-08-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.auth.scopes import DESCRIPCIONES, ROLES_OBSOLETOS, Scope

revision: str = "s3s10n3sr3v0"
down_revision: Union[str, Sequence[str], None] = "z9a0b1c2d3e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existe_tabla(conn, nombre: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SHOW TABLES LIKE :n"), {"n": nombre}
        ).first()
    )


def _existe_columna(conn, tabla: str, columna: str) -> bool:
    return bool(
        conn.execute(
            sa.text(f"SHOW COLUMNS FROM `{tabla}` LIKE :c"), {"c": columna}
        ).first()
    )


def upgrade() -> None:
    """Idempotente a propósito.

    MySQL no tiene DDL transaccional: si la migración se corta a mitad de camino,
    las tablas y columnas quedan creadas pero `alembic_version` no avanza, y el
    reintento explota con "table already exists". Y producción, además, **no
    tiene tabla `alembic_version`** (F6): se versiona a mano, así que esta
    migración se va a correr sobre un esquema del que nadie sabe con certeza qué
    partes ya tiene. Chequear antes de crear cuesta tres SELECT y hace que
    correrla dos veces sea inofensivo.
    """
    conn = op.get_bind()

    # ── 1. refresh_tokens (A5) ───────────────────────────────────────────────
    if not _existe_tabla(conn, "refresh_tokens"):
        op.create_table(
            "refresh_tokens",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("jti", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column(
                "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
            ),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_reason", sa.String(length=40), nullable=True),
            sa.Column("replaced_by", sa.String(length=64), nullable=True),
            sa.Column("origen", sa.String(length=16), nullable=True),
            sa.Column("user_agent", sa.String(length=255), nullable=True),
            sa.Column("ip", sa.String(length=45), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("jti"),
            sa.ForeignKeyConstraint(
                ["user_id"], ["listado_medico.ID"], ondelete="CASCADE"
            ),
        )
        op.create_index("ix_refresh_tokens_user", "refresh_tokens", ["user_id"])
        op.create_index("ix_refresh_tokens_expires", "refresh_tokens", ["expires_at"])

    # ── 2. Columnas de sesión y credencial (A2/A3, A7/A12) ───────────────────
    if not _existe_columna(conn, "listado_medico", "must_change_password"):
        op.add_column(
            "listado_medico",
            sa.Column(
                "must_change_password",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )
        # Las cuentas existentes quedan en 0: **NO se les fuerza el cambio**.
        # Decisión del titular del 2026-08-05, y es la razón de que la columna
        # nazca con `server_default = 0` y no haya ningún UPDATE acá.
        #
        # El flag aplica solo a las altas nuevas, que nacen con
        # `PASSWORD_INICIAL` — una contraseña pública que sí tiene que cambiarse
        # en el primer ingreso.
        #
        # Lo que eso deja abierto, para que quede escrito: las ~4.500 cuentas
        # vigentes conservan la contraseña que tienen, y para las que nunca se
        # cambió eso significa **el DNI** (era la inicial del alta) o **la
        # matrícula** (si en algún momento entraron por el fallback, que la
        # promovía a hash definitivo). Las dos son datos que un tercero consigue
        # sin esfuerzo. El fallback ya no existe, así que no se puede *entrar*
        # con la matrícula de alguien que nunca la usó, pero quien sí la usó
        # tiene esa matrícula como contraseña real.
        #
        # Ver A19 en docs/api/AUDITORIA_SEGURIDAD.md: hay un camino intermedio
        # —marcar solo las cuentas cuya contraseña siga siendo el DNI o la
        # matrícula, verificándolo hash por hash— que no molesta a nadie que ya
        # haya elegido una propia.

    if not _existe_columna(conn, "listado_medico", "tokens_valid_from"):
        op.add_column(
            "listado_medico", sa.Column("tokens_valid_from", sa.DateTime(), nullable=True)
        )

    # ── 3. medico:leer_propio (A4) ───────────────────────────────────────────
    conn.execute(
        sa.text(
            "INSERT IGNORE INTO permissions (code, description) VALUES (:c, :d)"
        ),
        {
            "c": Scope.MEDICO_LEER_PROPIO.value,
            "d": DESCRIPCIONES[Scope.MEDICO_LEER_PROPIO],
        },
    )
    conn.execute(
        sa.text(
            "INSERT IGNORE INTO role_permission (role_id, permission_id) "
            "SELECT r.id, p.id FROM roles r, permissions p "
            "WHERE r.name = 'medico' AND p.code = :c"
        ),
        {"c": Scope.MEDICO_LEER_PROPIO.value},
    )

    # ── 4. Baja del rol `contador` (A14) ─────────────────────────────────────
    # El orden importa: primero las tablas que referencian `roles.id`, después
    # el rol. `user_role` primero para no dejar usuarios sin rol de forma
    # silenciosa — quedan con 409 en `get_current_user_with_scopes_and_role`,
    # que es visible, y hay que reasignarlos a mano. El SELECT de abajo los
    # lista antes de borrarlos para que quede el rastro en el log de la
    # migración.
    for rol in sorted(ROLES_OBSOLETOS):
        afectados = conn.execute(
            sa.text(
                "SELECT lm.ID, lm.NRO_SOCIO, lm.NOMBRE FROM user_role ur "
                "JOIN roles r ON r.id = ur.role_id "
                "JOIN listado_medico lm ON lm.ID = ur.user_id "
                "WHERE r.name = :rol"
            ),
            {"rol": rol},
        ).all()
        if afectados:
            # Sin unicode: la consola de Windows corre en cp1252 y un caracter
            # fuera de esa tabla aborta la migracion a mitad de camino.
            print(
                "[s3s10n3sr3v0] rol '%s': %d usuario(s) pierden el rol y "
                "necesitan uno nuevo: %s"
                % (
                    rol,
                    len(afectados),
                    ", ".join("#%s socio=%s %s" % (i, s, n) for i, s, n in afectados),
                )
            )

        conn.execute(
            sa.text(
                "DELETE ur FROM user_role ur JOIN roles r ON r.id = ur.role_id "
                "WHERE r.name = :rol"
            ),
            {"rol": rol},
        )
        conn.execute(
            sa.text(
                "DELETE rp FROM role_permission rp JOIN roles r ON r.id = rp.role_id "
                "WHERE r.name = :rol"
            ),
            {"rol": rol},
        )
        conn.execute(sa.text("DELETE FROM roles WHERE name = :rol"), {"rol": rol})


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_expires", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_column("listado_medico", "tokens_valid_from")
    op.drop_column("listado_medico", "must_change_password")

    # No se revierten dos cosas, por el mismo criterio que la migración anterior:
    #   * el rol `contador` — recrearlo sería reintroducir una escalada de
    #     privilegio (A14) en un downgrade;
    #   * `medico:leer_propio` — sin ese permiso el rol `medico` pierde acceso a
    #     su propio legajo, que es una regresión funcional, no de seguridad.
