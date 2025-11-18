import json
import os
from pathlib import Path
import secrets
import shutil
from typing import Any, Dict, Tuple, Optional, Union
import re
from fastapi import UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Documento, ListadoMedico, Permission, RolePermission, UserPermission, UserRole
from datetime import date, datetime

UPLOAD_ROOT = "uploads" 
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


#region PARA ASIGNAR UNA ESPECIALIDAD A UN MEDICO

def parse_conceps_espec(raw):
    """
    raw puede venir como dict (JSON nativo) o como string (legado).
    Devuelve siempre {"espec": [...], "conceps": [...]}
    """
    if raw is None:
        return {"espec": [], "conceps": []}
    if isinstance(raw, dict):
        base = raw
    elif isinstance(raw, str):
        try:
            base = json.loads(raw) or {}
        except Exception:
            base = {}
    else:
        base = {}
    espec = base.get("espec")
    conceps = base.get("conceps")
    return {
        "espec": espec if isinstance(espec, list) else [],
        "conceps": conceps if isinstance(conceps, list) else [],
    }

def to_yyyy_mm_dd(s: str | None) -> str | None:
    """
    Acepta 'dd-MM-yyyy' o 'yyyy-MM-dd' y devuelve 'yyyy-MM-dd'.
    """
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return None

def build_espec_item(id_colegio: int, n_resolucion: str | None, fecha_resolucion: str | None, adjunto_id) -> dict:
    """
    Construye el item con el ORDEN y TIPOS que pediste:
    - adjunto     -> string (o None)
    - id_colegio  -> int
    - n_resolucion, fecha_resolucion -> string (o None); fecha en 'YYYY-MM-DD'
    """
    # adjunto siempre string si viene algo
    adj_str = None
    if adjunto_id is not None and str(adjunto_id).strip() != "":
        adj_str = str(adjunto_id).strip()

    return {
        "adjunto": adj_str,
        "id_colegio": int(id_colegio),
        "n_resolucion": (n_resolucion if n_resolucion else None),
        "fecha_resolucion": to_yyyy_mm_dd(fecha_resolucion),
    }

SPECIALTY_SLOTS = [
    "NRO_ESPECIALIDAD", "NRO_ESPECIALIDAD2", "NRO_ESPECIALIDAD3",
    "NRO_ESPECIALIDAD4", "NRO_ESPECIALIDAD5", "NRO_ESPECIALIDAD6",
]

def _parse_conceps_espec(raw) -> Dict[str, Any]:
    if raw is None:
        return {"espec": [], "conceps": []}
    if isinstance(raw, dict):
        return {"espec": list(raw.get("espec") or []), "conceps": list(raw.get("conceps") or [])}
    if isinstance(raw, (bytes, bytearray, memoryview)):
        s = bytes(raw).decode("utf-8", errors="ignore")
    else:
        s = str(raw)
    try:
        obj = json.loads(s) if s.strip() else {}
    except Exception:
        obj = {}
    return {"espec": list(obj.get("espec") or []), "conceps": list(obj.get("conceps") or [])}

def _dump_conceps_espec(obj: Dict[str, Any]) -> str:
    return json.dumps({"espec": obj.get("espec") or [], "conceps": obj.get("conceps") or []}, ensure_ascii=False)

def _find_slot_index(row, id_colegio: int | str) -> int | None:
    s = str(id_colegio).strip()
    for i, col in enumerate(SPECIALTY_SLOTS):
        v = getattr(row, col, None)
        if v is not None and str(v).strip() == s:
            return i
    return None

def _next_free_slot_index(row) -> int | None:
    for i, col in enumerate(SPECIALTY_SLOTS):
        if getattr(row, col, None) in (None, "", 0):
            return i
    return None

def parse_ddmmyyyy(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:10], fmt).date().isoformat()
        except Exception:
            pass
    return None

async def save_upload_for_medico(medico_id: int, up: UploadFile) -> Optional[Tuple[Documento, str]]:
    """
    Guarda UploadFile en uploads/medicos/{id}/ y crea un Documento (sin commit).
    Devuelve (doc_model, rel_path) o None si no hay archivo.
    """
    if not up or not up.filename:
        return None

    # carpeta destino
    dest_dir = os.path.join(UPLOAD_ROOT, "medicos", str(medico_id))
    os.makedirs(dest_dir, exist_ok=True)

    # nombre de archivo: timestamp + original "limpio"
    base = os.path.basename(up.filename)
    ts = int(datetime.now().timestamp())
    safe = "".join(ch for ch in base if ch.isalnum() or ch in "._- ").strip().replace(" ", "_")
    if not safe:
        safe = f"archivo_{ts}"
    filename = f"{ts}_{safe}"
    dest_path = os.path.join(dest_dir, filename)

    # escritura SINC en threadpool (no bloquear el loop)
    def _write_sync():
        up.file.seek(0)
        with open(dest_path, "wb") as f:
            shutil.copyfileobj(up.file, f, length=1024 * 1024)

    await run_in_threadpool(_write_sync)

    rel_path = dest_path.replace("\\", "/")
    size = os.path.getsize(dest_path)

    doc = Documento(
        medico_id=medico_id,
        label=None,                # se setea luego según slot ('resolucion_{n}')
        original_name=base,
        filename=filename,
        content_type=getattr(up, "content_type", None),
        size=size,
        path=rel_path,             # luego servís con f"/{rel_path}"
    )
    return doc, rel_path

def _parse_fecha_to_yyyy_mm_dd(s: Optional[str]) -> Optional[str]:
    """
    Acepta 'dd-MM-yyyy' o 'yyyy-MM-dd'. Devuelve 'yyyy-MM-dd' o None.
    """
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    # dd-MM-yyyy
    try:
        if "-" in s and len(s) == 10 and s[2] == "-" and s[5] == "-":
            dt = datetime.strptime(s, "%d-%m-%Y")
            return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    # yyyy-MM-dd
    try:
        dt = datetime.strptime(s[:10], "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None
#endregion
