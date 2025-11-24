# security.py
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from passlib.context import CryptContext
from app.db.models import ListadoMedico

_pwd = CryptContext(
    schemes=["pbkdf2_sha256", "bcrypt", "bcrypt_sha256"],
    deprecated=["bcrypt", "bcrypt_sha256"],
    pbkdf2_sha256__rounds=480_000,
)

def hash_password(plain: str) -> str:
    return _pwd.hash((plain or "").strip())

def verify_password(plain: str, hashed: Optional[str]) -> bool:
    try:
        return _pwd.verify((plain or "").strip(), (hashed or "").strip())
    except Exception:
        return False

def needs_update(hashed: Optional[str]) -> bool:
    try:
        return _pwd.needs_update((hashed or "").strip())
    except Exception:
        return True  # si es raro/desconocido, forzamos migración

async def verify_and_upgrade(
    db: AsyncSession,
    user: ListadoMedico,
    plain: str,
    *, allow_first_time_by_matricula: bool = True
) -> bool:
    pwd = (plain or "").strip()
    stored = (user.hashed_password or "").strip()

    # 1) Intento directo con lo que haya guardado (pbkdf2/bcrypt/bcrypt_sha256)
    if stored and verify_password(pwd, stored):
        # Si verificó y el hash está deprecado/antiguo → migrar a pbkdf2
        if needs_update(stored):
            user.hashed_password = hash_password(pwd)
            db.add(user)
            await db.commit()
        return True

    # 2) Primera vez: matrícula como contraseña (si lo permitís)
    if allow_first_time_by_matricula:
        matricula = (str(user.MATRICULA_PROV or "")).strip()
        if matricula and pwd == matricula:
            user.hashed_password = hash_password(pwd)
            db.add(user)
            await db.commit()
            return True

    return False