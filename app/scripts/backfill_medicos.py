import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any, Dict

from app.db.database import get_db
from app.db.models import ListadoMedico
from app.core.passwords import hash_password

DEFAULT_JSON: Dict[str, Any] = {"espec": [], "conceps": []}

def needs_json_fix(v) -> bool:
    if v is None:
        return True
    # si vino string vacío o "{}" o sin las claves
    if isinstance(v, str):
        return v.strip() == "" or v.strip() == "{}"
    if isinstance(v, dict):
        return not all(k in v for k in ("espec", "conceps"))
    return True

def looks_hashed(v: str | None) -> bool:
    if not v or not isinstance(v, str):
        return False
    # heurística simple: bcrypt/argon
    return v.startswith("$2a$") or v.startswith("$2b$") or v.startswith("$2y$") or v.startswith("$argon2")

async def run():
    async with get_db() as db:
        BATCH = 500
        offset = 0
        total_updates = 0

        while True:
            rows = (await db.execute(
                select(ListadoMedico).order_by(ListadoMedico.ID).offset(offset).limit(BATCH)
            )).scalars().all()
            if not rows:
                break

            changed = 0
            for m in rows:
                # 1) conceps_espec
                if needs_json_fix(getattr(m, "CONCEPS_ESPEC", None) or getattr(m, "conceps_espec", None)):
                    # acepta mayúsculas/minúsculas según tu modelo
                    if hasattr(m, "conceps_espec"):
                        m.conceps_espec = DEFAULT_JSON
                    else:
                        m.CONCEPS_ESPEC = DEFAULT_JSON
                    changed += 1

                # 2) hashed_password desde MATRICULA_PROV
                hp = getattr(m, "hashed_password", None) or getattr(m, "HASHED_PASSWORD", None)
                if not looks_hashed(hp):
                    raw = getattr(m, "MATRICULA_PROV", None) or getattr(m, "matricula_prov", None)
                    if raw:
                        new_hash = hash_password(str(raw))
                        if hasattr(m, "hashed_password"):
                            m.hashed_password = new_hash
                        else:
                            m.HASHED_PASSWORD = new_hash
                        changed += 1

            if changed:
                await db.commit()
                total_updates += changed

            offset += BATCH

        print(f"Backfill completo. Registros actualizados: {total_updates}")

if __name__ == "__main__":
    asyncio.run(run())