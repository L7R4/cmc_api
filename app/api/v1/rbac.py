from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, insert, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.db.models import Role, Permission, RolePermission, UserRole, UserPermission
from app.db.models import ListadoMedico
from app.auth.deps import require_scope
from app.utils.main import get_effective_permission_codes

router = APIRouter()
@router.get("/roles", dependencies=[Depends(require_scope("rbac:gestionar"))])
async def list_roles(db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(Role))
    return [{"name": r.name, "description": r.description} for r in rows.scalars().all()]

@router.get("/permissions", dependencies=[Depends(require_scope("rbac:gestionar"))])
async def list_permissions(db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(Permission))
    return [dict(id=r.id, code=r.code, description=r.description) for r in rows.scalars().all()]

# ---- ROLES ↔ PERMISOS ----
@router.get("/roles/{role_name}/permissions", dependencies=[Depends(require_scope("rbac:gestionar"))])
async def role_permissions(role_name: str, db: AsyncSession = Depends(get_db)):
    role = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one_or_none()
    if not role: raise HTTPException(404, "Rol no existe")
    q = (select(Permission.code, Permission.description)
         .join(RolePermission, RolePermission.permission_id == Permission.id)
         .where(RolePermission.role_id == role.id))
    rows = (await db.execute(q)).all()
    return [{"code": c, "description": d} for (c, d) in rows]

@router.post("/roles/{role_name}/permissions/{perm_code}", dependencies=[Depends(require_scope("rbac:gestionar"))])
async def add_perm_to_role(role_name: str, perm_code: str, db: AsyncSession = Depends(get_db)):
    role = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one_or_none()
    perm = (await db.execute(select(Permission).where(Permission.code == perm_code))).scalar_one_or_none()
    if not role or not perm: raise HTTPException(404, "Rol o permiso no existe")
    exists = (await db.execute(select(RolePermission).where(
        RolePermission.role_id == role.id, RolePermission.permission_id == perm.id))).first()
    if exists: return {"ok": True, "msg": "ya lo tenía"}
    await db.execute(insert(RolePermission).values(role_id=role.id, permission_id=perm.id))
    await db.commit()
    return {"ok": True}

@router.delete("/roles/{role_name}/permissions/{perm_code}", dependencies=[Depends(require_scope("rbac:gestionar"))])
async def remove_perm_from_role(role_name: str, perm_code: str, db: AsyncSession = Depends(get_db)):
    role = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one_or_none()
    perm = (await db.execute(select(Permission).where(Permission.code == perm_code))).scalar_one_or_none()
    if not role or not perm: raise HTTPException(404, "Rol o permiso no existe")
    await db.execute(delete(RolePermission).where(
        RolePermission.role_id == role.id, RolePermission.permission_id == perm.id))
    await db.commit()
    return {"ok": True}

# ---- USUARIOS ↔ ROLES ----
@router.get("/users/{user_id}/roles", dependencies=[Depends(require_scope("rbac:gestionar"))])
async def get_user_roles(user_id: int, db: AsyncSession = Depends(get_db)):
    q = (select(Role.name, Role.description)
         .join(UserRole, UserRole.role_id == Role.id)
         .where(UserRole.user_id == user_id))
    rows = (await db.execute(q)).all()
    return [{"name": n, "description": d} for (n, d) in rows]

@router.post("/users/{user_id}/roles/{role_name}", dependencies=[Depends(require_scope("rbac:gestionar"))])
async def add_role_to_user(user_id: int, role_name: str, db: AsyncSession = Depends(get_db)):
    user = await db.get(ListadoMedico, user_id)
    role = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one_or_none()
    if not user or not role: raise HTTPException(404, "Usuario o rol no existe")
    exists = (await db.execute(select(UserRole).where(
        UserRole.user_id == user_id, UserRole.role_id == role.id))).first()
    if exists: return {"ok": True, "msg": "ya tenía el rol"}
    await db.execute(insert(UserRole).values(user_id=user_id, role_id=role.id))
    await db.commit()
    return {"ok": True}

@router.delete("/users/{user_id}/roles/{role_name}", dependencies=[Depends(require_scope("rbac:gestionar"))])
async def remove_role_from_user(user_id: int, role_name: str, db: AsyncSession = Depends(get_db)):
    role = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one_or_none()
    if not role: raise HTTPException(404, "Rol no existe")
    await db.execute(delete(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == role.id))
    await db.commit()
    return {"ok": True}

# ---- OVERRIDES (allow/deny) ----
@router.get("/users/{user_id}/permissions/overrides", dependencies=[Depends(require_scope("rbac:gestionar"))])
async def list_user_overrides(user_id: int, db: AsyncSession = Depends(get_db)):
    q = (select(Permission.code, Permission.description, UserPermission.allow)
         .join(UserPermission, UserPermission.permission_id == Permission.id)
         .where(UserPermission.user_id == user_id))
    rows = (await db.execute(q)).all()
    return [{"code": c, "description": d, "allow": a} for (c, d, a) in rows]

@router.post("/users/{user_id}/permissions/{perm_code}", dependencies=[Depends(require_scope("rbac:gestionar"))])
async def set_user_permission_override(user_id: int, perm_code: str, allow: bool = True,
                                       db: AsyncSession = Depends(get_db)):
    perm = (await db.execute(select(Permission).where(Permission.code==perm_code))).scalar_one_or_none()
    if not perm: raise HTTPException(404, "Permiso no existe")
    await db.execute(delete(UserPermission).where(
        UserPermission.user_id==user_id, UserPermission.permission_id==perm.id))
    await db.execute(insert(UserPermission).values(user_id=user_id, permission_id=perm.id, allow=allow))
    await db.commit()
    return {"ok": True}

@router.delete("/users/{user_id}/permissions/{perm_code}", dependencies=[Depends(require_scope("rbac:gestionar"))])
async def clear_user_permission_override(user_id: int, perm_code: str, db: AsyncSession = Depends(get_db)):
    perm = (await db.execute(select(Permission).where(Permission.code==perm_code))).scalar_one_or_none()
    if not perm: raise HTTPException(404, "Permiso no existe")
    await db.execute(delete(UserPermission).where(
        UserPermission.user_id==user_id, UserPermission.permission_id==perm.id))
    await db.commit()
    return {"ok": True}

@router.get("/users/{user_id}/permissions/effective", dependencies=[Depends(require_scope("rbac:gestionar"))])
async def effective_permissions(user_id: int, db: AsyncSession = Depends(get_db)):
    codes = await get_effective_permission_codes(db, user_id)
    return {"permissions": codes}