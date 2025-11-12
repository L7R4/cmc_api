from passlib.context import CryptContext
from app.db.models import ListadoMedico
from sqlalchemy.ext.asyncio import AsyncSession
import re
from typing import Optional


_pwd = CryptContext(
    schemes=["pbkdf2_sha256"],
    deprecated="auto",
    pbkdf2_sha256__rounds=480_000,
)

_bcrypt_like = re.compile(r"^\$2[aby]\$")

def hash_password(plain: str) -> str:
    return _pwd.hash(plain or "")

def verify_password(plain: str, hashed: str) -> bool:
    return _pwd.verify(plain or "", hashed or "")

def is_bcrypt_hash(hashed: Optional[str]) -> bool:
    return bool(hashed and _bcrypt_like.match(hashed))


# contexto SOLO para verificar bcrypt como fallback
_bcrypt_ctx = CryptContext(schemes=["bcrypt_sha256"], deprecated="auto")

async def verify_and_upgrade(db: AsyncSession, user: ListadoMedico, plain: str) -> bool:
    stored = user.hashed_password or ""

    # 1) prueba PBKDF2 (principal)
    try:
        if verify_password(plain, stored):
            return True
    except Exception:
        pass

    # 2) si “parece” bcrypt, probá fallback y migra a PBKDF2
    if is_bcrypt_hash(stored):
        try:
            if _bcrypt_ctx.verify(plain or "", stored):
                user.hashed_password = hash_password(plain or "")
                await db.commit()
                return True
        except Exception:
            return False

    return False
