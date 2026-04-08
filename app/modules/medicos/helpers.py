import json
from typing import Any, Dict

from app.common.dates import to_yyyy_mm_dd

SPECIALTY_SLOTS = [
    "NRO_ESPECIALIDAD", "NRO_ESPECIALIDAD2", "NRO_ESPECIALIDAD3",
    "NRO_ESPECIALIDAD4", "NRO_ESPECIALIDAD5", "NRO_ESPECIALIDAD6",
]


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


def build_espec_item(id_colegio: int, n_resolucion: str | None, fecha_resolucion: str | None, adjunto_id) -> dict:
    adj_str = None
    if adjunto_id is not None and str(adjunto_id).strip() != "":
        adj_str = str(adjunto_id).strip()

    return {
        "adjunto": adj_str,
        "id_colegio": int(id_colegio),
        "n_resolucion": (n_resolucion if n_resolucion else None),
        "fecha_resolucion": to_yyyy_mm_dd(fecha_resolucion),
    }


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
