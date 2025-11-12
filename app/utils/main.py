from typing import Tuple, Optional, Union
import re
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Permission, RolePermission, UserPermission, UserRole
from datetime import date, datetime

_PERIODO_RX = re.compile(r"^\s*(\d{4})[-/]?(\d{1,2})\s*$")

def normalizar_periodo(periodo_id: str) -> Tuple[int, int, str]:
    m = _PERIODO_RX.match(periodo_id or "")
    if not m:
        raise ValueError("periodo_id inválido; use 'YYYY-MM'")
    y, mth = int(m.group(1)), int(m.group(2))
    if y < 1900 or y > 3000 or not (1 <= mth <= 12):
        raise ValueError("periodo fuera de rango")
    return y, mth, f"{y:04d}-{mth:02d}"



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

def _parse_date(s: Optional[Union[str, date, datetime]]):
    if not s:
        return None
    if isinstance(s, date) and not isinstance(s, datetime):
        return s
    if isinstance(s, datetime):
        return s.date()

    s = str(s).strip()

    # ISO puros: YYYY-MM-DD
    try:
        return date.fromisoformat(s)
    except Exception:
        pass

    # ISO con tiempo/zona: YYYY-MM-DDTHH:MM[:SS][.fff][Z|+hh:mm]
    try:
        s2 = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s2).date()
    except Exception:
        pass

    # Formatos comunes que queremos soportar
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue

    return None