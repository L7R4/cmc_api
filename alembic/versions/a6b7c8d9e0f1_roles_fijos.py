"""RBAC: línea de base fija de cada rol, definida por el Colegio

Hasta acá `ROLES` era "la línea de base documentada" y se aceptaba que
`role_permission` divergiera. El Colegio fijó la composición de los cinco roles,
así que esta migración **iguala** la tabla al dict: borra lo que sobra e inserta
lo que falta, rol por rol. Roles que no estén en `ROLES` no se tocan.

Lo que cambia respecto de lo que había:

  1. **Los cuatro de `CRITICOS` pasan a ir por rol.** `nomenclador:masivo` y
     `medico:editar_bancario` a `facturador`; `pago:reabrir` y
     `medico:editar_bancario` a `liquidador`; los cuatro a `admin`. Antes se
     otorgaban solo por `user_permission` para dejar constancia nominal.

     **El ajuste por persona no se pierde y es lo que sostiene la decisión**:
     `user_permission` se resuelve encima del rol, y `allow=False` **deniega**
     aunque el rol conceda. O sea que sacarle el CBU a alguien puntual sigue
     siendo una fila, sin tocarle el rol. `test_permisos_criticos_no_van_por_rol`
     se eliminó junto con la regla.

  2. **Dos scopes nuevos para el socio**: `facturacion:leer_propio` y
     `liquidacion:leer_propio`. El Colegio los pidió como `*:ver_propio`; se
     nombran `leer_propio` para que sigan la convención del catálogo —
     `medico:leer_propio` ya existía y `ver` no está en el conjunto cerrado de
     acciones que valida `test_convencion_de_nombres`.

     `facturacion:leer_propio` **reemplaza** a `facturacion:leer` en el rol
     `medico`. Antes el mismo scope significaba dos cosas según quién lo tuviera
     y lo que las separaba era que el handler llamara a `filtro_socio()`; ahora
     el alcance se lee en el token. `liquidacion:leer_propio` todavía no gatea
     ningún endpoint: la API no tiene un "mi liquidación", se da de alta para
     cuando el front lo necesite.

  3. **El rol `medico` pierde `padron:leer` y `facturacion:cargar`.** Para que la
     carga del portal no se rompa, `POST /api/facturacion/medico/prestaciones`
     pasó en la matriz de `facturacion:cargar` a `validacion:cargar`, que es el
     permiso del prestador y el rol conserva. Las pantallas Validaciones y
     Consulta de precios siguen funcionando (`validacion:cargar` y
     `nomenclador:leer`).

  4. **`facturador` y `liquidador` se redefinen enteros** según las listas del
     Colegio. Lo que pierden está en el docstring de `upgrade()`, porque son
     pantallas que dejan de andar para esos roles y conviene que quede escrito.

  5. `editor_web` no cambia: la lista del Colegio no incluía `aviso:gestionar`,
     `beneficio:gestionar` ni `catalogo:leer`, pero se decidió conservarlos.

No toca `user_permission`: los overrides nominales que ya existan siguen como
están, incluso los que ahora quedan redundantes porque el rol concede lo mismo.

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-08-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.auth.scopes import DESCRIPCIONES, ROLES, Scope, codigos_de_rol

revision: str = "a6b7c8d9e0f1"
down_revision: Union[str, Sequence[str], None] = "f5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Iguala `role_permission` a `ROLES`.

    Lo que **pierde** cada rol respecto de la composición anterior, para que no
    sea una sorpresa cuando alguien reporte una pantalla en 403:

      * `medico`: `padron:leer` (deja de ver su padrón por obra social),
        `facturacion:leer` (reemplazado por `facturacion:leer_propio`) y
        `facturacion:cargar` (reemplazado por `validacion:cargar`).
      * `facturador`: `padron:editar` (edición de padrones; conserva la lectura)
        y `solicitud:leer` (bandeja de solicitudes).
      * `liquidador`: `facturacion:leer`, `facturacion:periodo`, `catalogo:leer`
        y `nomenclador:leer`.

    `contenido:leer` lo llevan **todos** los roles por decisión del Colegio, y
    `padron:leer` lo conservan `facturador` y `liquidador` — solo `medico` lo
    pierde.

    Si alguna de esas hace falta, se devuelve desde la pantalla de
    administración o agregándola a `ROLES` en una migración nueva.
    """
    conn = op.get_bind()

    # Los dos scopes nuevos tienen que existir en `permissions` antes de
    # asignarse. El resto del catálogo ya lo insertó `f5a6b7c8d9e0`.
    for scope in (Scope.FACTURACION_LEER_PROPIO, Scope.LIQUIDACION_LEER_PROPIO):
        conn.execute(
            sa.text(
                "INSERT IGNORE INTO permissions (code, description) "
                "VALUES (:code, :desc)"
            ),
            {"code": scope.value, "desc": DESCRIPCIONES[scope]},
        )

    for rol in ROLES:
        codigos = codigos_de_rol(rol)

        # Sobrante: todo lo que el rol tiene en la tabla y no está en `ROLES`.
        # `NOT IN` con lista vacía es un error de sintaxis en MySQL, y un rol
        # sin permisos no es un caso real, pero el guard sale barato.
        if codigos:
            conn.execute(
                sa.text(
                    "DELETE rp FROM role_permission rp "
                    "JOIN roles r ON r.id = rp.role_id "
                    "JOIN permissions p ON p.id = rp.permission_id "
                    "WHERE r.name = :rol AND p.code NOT IN :codigos"
                ).bindparams(sa.bindparam("codigos", expanding=True)),
                {"rol": rol, "codigos": codigos},
            )
        else:
            conn.execute(
                sa.text(
                    "DELETE rp FROM role_permission rp "
                    "JOIN roles r ON r.id = rp.role_id WHERE r.name = :rol"
                ),
                {"rol": rol},
            )

        # Faltante.
        for code in codigos:
            conn.execute(
                sa.text(
                    "INSERT IGNORE INTO role_permission (role_id, permission_id) "
                    "SELECT r.id, p.id FROM roles r, permissions p "
                    "WHERE r.name = :rol AND p.code = :code"
                ),
                {"rol": rol, "code": code},
            )


def downgrade() -> None:
    """No reconstruye la composición vieja de los roles.

    `upgrade()` es un reemplazo, no un delta: para revertirlo habría que
    conservar en algún lado los ~90 pares (rol, permiso) previos, y ese estado
    ya diverge entre dev y producción. Si hay que volver atrás, la vía es
    escribir la composición deseada en `ROLES` y correr una migración nueva —
    que además deja el cambio revisable en el diff, cosa que un `downgrade`
    genérico no haría.

    Lo único que sí se revierte son los dos scopes nuevos, que no existían.
    """
    conn = op.get_bind()
    for scope in (Scope.FACTURACION_LEER_PROPIO, Scope.LIQUIDACION_LEER_PROPIO):
        conn.execute(
            sa.text("DELETE FROM permissions WHERE code = :code"),
            {"code": scope.value},
        )
