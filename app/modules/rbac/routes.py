"""Administración de roles y permisos.

## Por qué cada mutación desloguea a alguien (A12)

Los scopes se congelan en el JWT al emitirlo: `get_current_user` los lee del
token y no consulta la base. Eso es lo que hace que autorizar cueste cero
consultas, y también lo que hacía que **quitar un permiso no tuviera efecto**
hasta que venciera el access token del usuario. Con `ACCESS_MINUTES = 15`, quien
acababa de perder `pago:reabrir` seguía pudiendo reabrir pagos durante quince
minutos, y con su refresh vivo se sacaba tokens nuevos por otros quince días.

Para un cambio administrativo rutinario esos 15 minutos daban igual. Para el
caso que importa —bajarle los permisos a alguien *porque hubo un problema*— era
justamente el momento en que no servía.

Por eso toda mutación de acá llama a `revocar_sesiones()` sobre el usuario
afectado: sus refresh quedan revocados y `tokens_valid_from` invalida los access
ya emitidos. El usuario recibe 401 en la request siguiente, el front lo manda al
login, y al volver a entrar tiene los permisos nuevos.

Los cambios sobre un **rol** (no sobre un usuario) alcanzan a todos los que lo
tienen, así que se revocan todos. Es la operación más cara del módulo y también
la más rara: se cambian los permisos de un rol una vez cada varios meses.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import get_effective_permission_codes
from app.auth.sessions import revocar_sesiones
from app.db.database import get_db
from app.db.models import ListadoMedico, Permission, Role, RolePermission, UserPermission, UserRole

router = APIRouter()


async def _revocar_usuarios_del_rol(db: AsyncSession, role_id: int) -> int:
    """Desloguea a todos los que tienen el rol. Devuelve cuántos."""
    ids = [
        uid for (uid,) in (
            await db.execute(select(UserRole.user_id).where(UserRole.role_id == role_id))
        ).all()
    ]
    for uid in ids:
        await revocar_sesiones(db, uid, motivo="cambio_permisos")
    return len(ids)


@router.get("/roles")
async def list_roles(db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(Role))
    return [{"name": r.name, "description": r.description} for r in rows.scalars().all()]


@router.get("/permissions")
async def list_permissions(db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(Permission))
    return [dict(id=r.id, code=r.code, description=r.description) for r in rows.scalars().all()]


@router.get("/roles/{role_name}/permissions")
async def role_permissions(role_name: str, db: AsyncSession = Depends(get_db)):
    role = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one_or_none()
    if not role:
        raise HTTPException(404, "Rol no existe")
    q = (
        select(Permission.code, Permission.description)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role.id)
    )
    rows = (await db.execute(q)).all()
    return [{"code": c, "description": d} for (c, d) in rows]


@router.post("/roles/{role_name}/permissions/{perm_code}")
async def add_perm_to_role(role_name: str, perm_code: str, db: AsyncSession = Depends(get_db)):
    role = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one_or_none()
    perm = (await db.execute(select(Permission).where(Permission.code == perm_code))).scalar_one_or_none()
    if not role or not perm:
        raise HTTPException(404, "Rol o permiso no existe")
    exists = (await db.execute(select(RolePermission).where(
        RolePermission.role_id == role.id, RolePermission.permission_id == perm.id))).first()
    if exists:
        return {"ok": True, "msg": "ya lo tenía"}
    await db.execute(insert(RolePermission).values(role_id=role.id, permission_id=perm.id))
    await db.commit()
    # También al conceder: si no, el usuario ve el permiso nuevo recién dentro de
    # 15 minutos y lo reporta como "no me anda".
    afectados = await _revocar_usuarios_del_rol(db, role.id)
    return {"ok": True, "sesiones_cerradas": afectados}


@router.delete("/roles/{role_name}/permissions/{perm_code}")
async def remove_perm_from_role(role_name: str, perm_code: str, db: AsyncSession = Depends(get_db)):
    role = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one_or_none()
    perm = (await db.execute(select(Permission).where(Permission.code == perm_code))).scalar_one_or_none()
    if not role or not perm:
        raise HTTPException(404, "Rol o permiso no existe")
    await db.execute(delete(RolePermission).where(
        RolePermission.role_id == role.id, RolePermission.permission_id == perm.id))
    await db.commit()
    afectados = await _revocar_usuarios_del_rol(db, role.id)
    return {"ok": True, "sesiones_cerradas": afectados}


@router.get("/users/{user_id}/roles")
async def get_user_roles(user_id: int, db: AsyncSession = Depends(get_db)):
    q = (
        select(Role.name, Role.description)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
    )
    rows = (await db.execute(q)).all()
    return [{"name": n, "description": d} for (n, d) in rows]


@router.post("/users/{user_id}/roles/{role_name}")
async def add_role_to_user(user_id: int, role_name: str, db: AsyncSession = Depends(get_db)):
    user = await db.get(ListadoMedico, user_id)
    role = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one_or_none()
    if not user or not role:
        raise HTTPException(404, "Usuario o rol no existe")
    exists = (await db.execute(select(UserRole).where(
        UserRole.user_id == user_id, UserRole.role_id == role.id))).first()
    if exists:
        return {"ok": True, "msg": "ya tenía el rol"}
    await db.execute(insert(UserRole).values(user_id=user_id, role_id=role.id))
    await db.commit()
    await revocar_sesiones(db, user_id, motivo="cambio_permisos")
    return {"ok": True, "sesiones_cerradas": 1}


@router.delete("/users/{user_id}/roles/{role_name}")
async def remove_role_from_user(user_id: int, role_name: str, db: AsyncSession = Depends(get_db)):
    role = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one_or_none()
    if not role:
        raise HTTPException(404, "Rol no existe")
    await db.execute(delete(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == role.id))
    await db.commit()
    # El más importante de todos: quitarle el rol a alguien tiene que cortarle
    # el acceso ahora, no cuando venza su token.
    await revocar_sesiones(db, user_id, motivo="cambio_permisos")
    return {"ok": True, "sesiones_cerradas": 1}


@router.get("/users/{user_id}/permissions/overrides")
async def list_user_overrides(user_id: int, db: AsyncSession = Depends(get_db)):
    q = (
        select(Permission.code, Permission.description, UserPermission.allow)
        .join(UserPermission, UserPermission.permission_id == Permission.id)
        .where(UserPermission.user_id == user_id)
    )
    rows = (await db.execute(q)).all()
    return [{"code": c, "description": d, "allow": a} for (c, d, a) in rows]


@router.post("/users/{user_id}/permissions/{perm_code}")
async def set_user_permission_override(user_id: int, perm_code: str, allow: bool = True, db: AsyncSession = Depends(get_db)):
    perm = (await db.execute(select(Permission).where(Permission.code == perm_code))).scalar_one_or_none()
    if not perm:
        raise HTTPException(404, "Permiso no existe")
    await db.execute(delete(UserPermission).where(UserPermission.user_id == user_id, UserPermission.permission_id == perm.id))
    await db.execute(insert(UserPermission).values(user_id=user_id, permission_id=perm.id, allow=allow))
    await db.commit()
    # Vale para las dos direcciones: `allow=False` es un deny nominal y tiene que
    # hacerse efectivo de inmediato.
    await revocar_sesiones(db, user_id, motivo="cambio_permisos")
    return {"ok": True, "sesiones_cerradas": 1}


@router.delete("/users/{user_id}/permissions/{perm_code}")
async def clear_user_permission_override(user_id: int, perm_code: str, db: AsyncSession = Depends(get_db)):
    perm = (await db.execute(select(Permission).where(Permission.code == perm_code))).scalar_one_or_none()
    if not perm:
        raise HTTPException(404, "Permiso no existe")
    await db.execute(delete(UserPermission).where(UserPermission.user_id == user_id, UserPermission.permission_id == perm.id))
    await db.commit()
    await revocar_sesiones(db, user_id, motivo="cambio_permisos")
    return {"ok": True, "sesiones_cerradas": 1}


@router.get("/users/{user_id}/permissions/effective")
async def effective_permissions(user_id: int, db: AsyncSession = Depends(get_db)):
    codes = await get_effective_permission_codes(db, user_id)
    return {"permissions": codes}
