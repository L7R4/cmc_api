"""Quién es el usuario de una sesión: rol, permisos y lo que ve el front.

Antes cada punto que abría o renovaba una sesión (login web, refresh web, `/me`,
login y refresh del app) armaba el usuario por su cuenta. Coincidían de casualidad
y cada campo nuevo había que acordarse de agregarlo en cuatro lugares.

Las reglas que viven acá:

* **Acceso**: sólo entra una cuenta `estado_cuenta='activo'` con al menos un rol.
  Sin rol no hay permisos, y antes eso aparecía recién en `/me` como un 409.
* **Rol principal**: si alguien tiene varios, gana el de más alcance según
  `JERARQUIA_ROLES`. Antes era el primero que devolvía la base, sin orden.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import get_effective_permission_codes
from app.db.models import ListadoMedico, Role, UserRole
from app.db.models.medico import ESTADO_ACTIVO, mensaje_cuenta_inactiva

# De más a menos alcance. Un rol que no figure acá queda último.
JERARQUIA_ROLES = ("admin", "liquidador", "facturador", "editor_web", "medico")

# El que recibe toda cuenta activa que no tiene otro (socios y organizaciones).
ROL_BASE = "medico"

MENSAJE_SIN_ROL = "Tu cuenta no tiene un rol asignado. Comunicate con el Colegio."

# Columnas de especialidad en orden de prioridad (la principal NO lleva sufijo "1").
_CAMPOS_ESPECIALIDAD = (
    "NRO_ESPECIALIDAD",
    "NRO_ESPECIALIDAD2",
    "NRO_ESPECIALIDAD3",
    "NRO_ESPECIALIDAD4",
    "NRO_ESPECIALIDAD5",
    "NRO_ESPECIALIDAD6",
)


class SinAcceso(Exception):
    """La cuenta existe y la contraseña es correcta, pero no puede operar."""

    def __init__(self, codigo: str, mensaje: str) -> None:
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje


def especialidades_ordenadas(medico: ListadoMedico) -> list[int]:
    """ID_COLEGIO_ESPE cargados, principal primero; omite los slots vacíos."""
    return [int(esp) for campo in _CAMPOS_ESPECIALIDAD if (esp := getattr(medico, campo, None))]


async def rol_principal(db: AsyncSession, user_id: int) -> str | None:
    nombres = (
        await db.execute(
            select(Role.name)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        )
    ).scalars().all()
    if not nombres:
        return None
    orden = {nombre: i for i, nombre in enumerate(JERARQUIA_ROLES)}
    return min(nombres, key=lambda n: (orden.get(n, len(orden)), n))


async def asignar_rol_base_si_falta(db: AsyncSession, user_id: int) -> None:
    """Le da el rol `medico` a quien no tiene ninguno. No hace commit.

    Se llama donde una cuenta pasa a `activo` (aprobar la solicitud, alta de
    clínica, reactivar desde el legajo): una cuenta activa sin rol no puede entrar,
    y así quedaron las 19 clínicas dadas de alta antes de esto.
    """
    tiene = (
        await db.execute(select(UserRole.role_id).where(UserRole.user_id == user_id).limit(1))
    ).first()
    if tiene:
        return
    role_id = (await db.execute(select(Role.id).where(Role.name == ROL_BASE))).scalar_one_or_none()
    if role_id is None:
        raise RuntimeError(f"No existe el rol '{ROL_BASE}' en la tabla roles")
    db.add(UserRole(user_id=user_id, role_id=role_id))


def exigir_acceso(medico: ListadoMedico, role: str | None) -> None:
    """Lanza `SinAcceso` si la cuenta no está activa o no tiene rol."""
    if medico.estado_cuenta != ESTADO_ACTIVO:
        raise SinAcceso("cuenta_inactiva", mensaje_cuenta_inactiva(medico.estado_cuenta))
    if not role:
        raise SinAcceso("sin_rol", MENSAJE_SIN_ROL)


async def construir_usuario(db: AsyncSession, medico: ListadoMedico) -> tuple[dict, list[str], str]:
    """Valida el acceso y devuelve `(usuario, scopes, rol)`.

    `usuario` es lo que reciben el front y el app en login, refresh y `/me`.
    Los scopes y el rol se leen siempre de la base, nunca del token.
    """
    role = await rol_principal(db, medico.ID)
    exigir_acceso(medico, role)
    scopes = await get_effective_permission_codes(db, medico.ID)
    usuario = {
        "id": int(medico.ID),
        "nro_socio": int(medico.NRO_SOCIO),
        "nombre": medico.NOMBRE,
        "scopes": scopes,
        "role": role,
        "especialidades": especialidades_ordenadas(medico),
        "es_organizacion": bool(medico.es_organizacion),
        "adherente": bool(medico.adherente),
        # El cliente manda a la pantalla de cambio de contraseña antes que a
        # ningún otro lado: la cuenta todavía tiene la clave inicial, que es pública.
        "must_change_password": bool(medico.must_change_password),
    }
    return usuario, scopes, role
