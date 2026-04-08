from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Permission, RolePermission, UserPermission, UserRole


async def get_effective_permission_codes(db: AsyncSession, user_id: int) -> list[str]:
    # Permisos por roles
    q_roles = (
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == user_id)
    )
    role_perm_codes = [row[0] for row in (await db.execute(q_roles)).all()]

    # Overrides allow
    q_allow = (
        select(Permission.code)
        .join(UserPermission, UserPermission.permission_id == Permission.id)
        .where(UserPermission.user_id == user_id, UserPermission.allow == True)
    )
    allow_codes = [row[0] for row in (await db.execute(q_allow)).all()]

    # Overrides deny
    q_deny = (
        select(Permission.code)
        .join(UserPermission, UserPermission.permission_id == Permission.id)
        .where(UserPermission.user_id == user_id, UserPermission.allow == False)
    )
    deny_codes = [row[0] for row in (await db.execute(q_deny)).all()]

    effective = (set(role_perm_codes) | set(allow_codes)) - set(deny_codes)
    return sorted(effective)
