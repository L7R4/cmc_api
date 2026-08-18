"""RBAC: sincroniza `permissions` con el catálogo de scopes.py

Cierra la Etapa 3 de `docs/api/RBAC_PROPUESTA.md` §7 y arregla lo que la
limpieza manual de la tabla dejó a medio camino. Cinco cosas, todas idempotentes:

  1. **Alta de `reporte:leer`.** `/api/reportes/*` exigía
     `require_scope("facturas:ver")`, un código del catálogo viejo que la
     limpieza borró de `permissions`. Nadie lo llevaba en el token, así que el
     módulo entero respondía 403 — admin incluido. No se reusa
     `facturacion:leer` porque el rol `medico` lo tiene: ver la nota de
     `Scope.REPORTE_LEER`.

  2. **Baja de tres códigos obsoletos que sobrevivieron** a la limpieza:
     `auditoria:ver`, `liquidacion:procesar` y `liquidacion:ver`. Los FK de
     `role_permission` y `user_permission` son ON DELETE CASCADE en las dos
     bases, así que las asignaciones se van con ellos.

     `liquidacion:ver` figura en el `ALIAS_LEGACY` del panel
     (`src/app/auth/scopes.ts`) como equivalente viejo de `liquidacion:leer`,
     pero `hasScope()` acepta cualquiera de los dos y todo el que tiene el
     viejo tiene el nuevo — `admin` y `liquidador` en las dos bases. Borrarlo
     no apaga ninguna pantalla. `liquidacion:procesar` y `auditoria:ver` no
     aparecen en ningún consumidor.

     **Dos códigos que parecían obsoletos y NO se borran**, porque el criterio
     "no lo nombra ningún `require_scope`" resultó insuficiente — los mira el
     front, que no está en este repo:

       * `system_new:access` — `cmc_has('system_new:access')` en tres pantallas
         del legacy (`principal.php`, `principal3.php`,
         `calcular_valores_colegio.php`) dibuja el link "INGRESAR NUEVO
         SISTEMA". Borrarlo dejaba a los 19 admins de producción sin puerta de
         entrada al panel nuevo. Se promueve a `Scope.SYSTEM_NEW_ACCESS`.
       * `web:editor` — está en `WEB_EDITOR_SCOPES_LEGACY` de
         `src/app/auth/roles.ts`, y es lo único que hace que `isWebEditor()`
         sea verdadero para el socio 43747 ("ALINE SISTEMA"), que tiene rol
         `medico` y ninguna otra vía. Su acceso hoy está a medias — el panel le
         muestra las pantallas de contenido y la API le rechaza las escrituras
         porque no tiene `contenido:editar` — pero eso se arregla dándole el
         rol `editor_web`, no borrándole el permiso por debajo. Queda en la
         tabla hasta que alguien decida qué corresponde para ese usuario.

  3. **`auditoria:purgar` fuera del rol `admin`.** Está en `CRITICOS`: se
     concede nominalmente por `user_permission`, nunca por rol. El invariante lo
     verifica `test_permisos_criticos_no_van_por_rol`, que hoy falla contra la
     base. El socio 4598 ya lo tiene como override, así que nadie pierde acceso.

  4. **Lo que le falta a cada rol** según `ROLES`:

       * `admin` ← `reporte:leer` y `system_new:access`.
       * `facturador` ← `validacion:cargar` (sólo en producción). El rol carga
         validaciones en nombre de un médico desde la pantalla de Validaciones
         y ya tiene `medico:leer`, que es lo que `ownership.socio_objetivo`
         exige para pedir la matrícula de otro; sin `validacion:cargar` esa
         pantalla no le funciona. Hoy afecta a 1 usuario.

  4b. **El socio 43747 pasa del rol `medico` al rol `editor_web`.** Es una
     cuenta de sistema ("ALINE SISTEMA"), no un colegiado: matrícula igual al
     número de socio, DNI 20202020 y cero prestaciones. Venía con `medico` más
     un override suelto de `web:editor`, combinación que le daba las pantallas
     de contenido en el panel y ninguna de las escrituras en la API. Con el rol
     correcto pasa a tener `contenido:leer/editar`, `aviso:gestionar`,
     `beneficio:gestionar` y `catalogo:leer`, que es lo que la tarea necesita.

  5. **Descripciones normalizadas.** La tabla tenía las tildes doble-codificadas
     en la mayoría de las filas (bytes `C3 83 C2 B3` donde va una `ó`): entraron
     por una conexión con el charset mal puesto y se leían mal desde cualquier
     cliente utf8mb4, o sea desde la pantalla de administración. El UPDATE corre
     sobre todo el catálogo y las reescribe desde `DESCRIPCIONES`, que es la
     fuente. También arrastra cualquier otra divergencia de texto.

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-08-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.auth.scopes import DESCRIPCIONES, Scope

revision: str = "f5a6b7c8d9e0"
down_revision: Union[str, Sequence[str], None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Los que sobrevivieron a la limpieza manual de `permissions`. Las dos bases no
# tienen exactamente los mismos: dev conserva `auditoria:ver` y producción no.
# El DELETE es idempotente, así que la lista es la unión de ambas.
A_BORRAR = (
    "auditoria:ver",
    "liquidacion:procesar",
    "liquidacion:ver",
    # Unificado en `medico:leer`. Se puede borrar antes de desplegar el código
    # nuevo: el que corre hoy en producción lo **declara** en `scopes.py` pero
    # no lo evalúa en ningún lado (ahí ni siquiera existe `puede_ver_sensible`),
    # así que sacarlo de la tabla no cambia ninguna respuesta.
    "medico:leer_sensible",
)

# Cuenta de sistema, no un colegiado: matrícula igual al número de socio, DNI
# 20202020 y cero prestaciones. Edita el portal, y para eso el rol `medico` no
# le sirve. Se **reemplaza** en vez de sumarse porque `get_user_role()` hace
# `LIMIT 1` sin `ORDER BY`: con dos roles, el `role` del token —y con él el
# ruteo del panel, que mira `user.role === "editor_web"` y `medicoCanAccess()`—
# quedaría a merced del plan de ejecución de MySQL.
SOCIO_A_EDITOR_WEB = 43747

# (rol, código) que hay que quitar de `role_permission` sin borrar el permiso.
DESASIGNAR = (("admin", "auditoria:purgar"),)

# (rol, código) que hay que agregar. Cubre lo que le falta a dev, a producción o
# a las dos — el INSERT ... SELECT es idempotente y no hace nada si ya está.
ASIGNAR = (
    ("admin", Scope.REPORTE_LEER.value),
    ("admin", Scope.SYSTEM_NEW_ACCESS.value),
    ("facturador", Scope.VALIDACION_CARGAR.value),
)


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1 y 5. Catálogo completo: alta de los que falten, descripción al día ──
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

    # ── 2. Baja de los obsoletos (las asignaciones caen por CASCADE) ─────────
    for code in A_BORRAR:
        conn.execute(
            sa.text("DELETE FROM permissions WHERE code = :code"), {"code": code}
        )

    # ── 3. CRITICOS fuera de los roles ───────────────────────────────────────
    for rol, code in DESASIGNAR:
        conn.execute(
            sa.text(
                "DELETE rp FROM role_permission rp "
                "JOIN roles r ON r.id = rp.role_id "
                "JOIN permissions p ON p.id = rp.permission_id "
                "WHERE r.name = :rol AND p.code = :code"
            ),
            {"rol": rol, "code": code},
        )

    # ── 4b. El socio 43747 pasa de `medico` a `editor_web` ───────────────────
    # Por NRO_SOCIO y no por ID: la PK difiere entre dev y producción.
    conn.execute(
        sa.text(
            "DELETE ur FROM user_role ur "
            "JOIN listado_medico m ON m.ID = ur.user_id "
            "JOIN roles r ON r.id = ur.role_id "
            "WHERE m.NRO_SOCIO = :socio AND r.name = 'medico'"
        ),
        {"socio": SOCIO_A_EDITOR_WEB},
    )
    conn.execute(
        sa.text(
            "INSERT IGNORE INTO user_role (user_id, role_id) "
            "SELECT m.ID, r.id FROM listado_medico m, roles r "
            "WHERE m.NRO_SOCIO = :socio AND r.name = 'editor_web'"
        ),
        {"socio": SOCIO_A_EDITOR_WEB},
    )

    # ── 4. Lo que le faltaba a `admin` ───────────────────────────────────────
    for rol, code in ASIGNAR:
        conn.execute(
            sa.text(
                "INSERT IGNORE INTO role_permission (role_id, permission_id) "
                "SELECT r.id, p.id FROM roles r, permissions p "
                "WHERE r.name = :rol AND p.code = :code"
            ),
            {"rol": rol, "code": code},
        )


def downgrade() -> None:
    """Revierte lo reversible.

    Los permisos obsoletos **no vuelven**: eran códigos muertos y reconstruirlos
    implicaría adivinar a qué roles estaban asignados. Lo que sí se revierte es
    lo que tiene sentido revertir — `reporte:leer` y los ajustes de
    `role_permission` — para poder bajar la migración sin dejar los roles
    distintos de como estaban.

    `system_new:access` tampoco se toca en el downgrade: existía antes de esta
    migración y tiene que seguir existiendo después de bajarla.
    """
    conn = op.get_bind()

    # El socio 43747 vuelve a `medico`.
    conn.execute(
        sa.text(
            "DELETE ur FROM user_role ur "
            "JOIN listado_medico m ON m.ID = ur.user_id "
            "JOIN roles r ON r.id = ur.role_id "
            "WHERE m.NRO_SOCIO = :socio AND r.name = 'editor_web'"
        ),
        {"socio": SOCIO_A_EDITOR_WEB},
    )
    conn.execute(
        sa.text(
            "INSERT IGNORE INTO user_role (user_id, role_id) "
            "SELECT m.ID, r.id FROM listado_medico m, roles r "
            "WHERE m.NRO_SOCIO = :socio AND r.name = 'medico'"
        ),
        {"socio": SOCIO_A_EDITOR_WEB},
    )

    conn.execute(
        sa.text(
            "DELETE FROM permissions WHERE code = :code"
        ),
        {"code": Scope.REPORTE_LEER.value},
    )

    for rol, code in ASIGNAR:
        if code in (Scope.REPORTE_LEER.value, Scope.SYSTEM_NEW_ACCESS.value):
            continue  # el primero se fue con el DELETE; el segundo es preexistente
        conn.execute(
            sa.text(
                "DELETE rp FROM role_permission rp "
                "JOIN roles r ON r.id = rp.role_id "
                "JOIN permissions p ON p.id = rp.permission_id "
                "WHERE r.name = :rol AND p.code = :code"
            ),
            {"rol": rol, "code": code},
        )

    for rol, code in DESASIGNAR:
        conn.execute(
            sa.text(
                "INSERT IGNORE INTO role_permission (role_id, permission_id) "
                "SELECT r.id, p.id FROM roles r, permissions p "
                "WHERE r.name = :rol AND p.code = :code"
            ),
            {"rol": rol, "code": code},
        )
