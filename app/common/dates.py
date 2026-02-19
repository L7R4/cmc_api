import re
from datetime import date, datetime
from typing import Optional, Tuple, Union

_PERIODO_RX = re.compile(r"^\s*(\d{4})[-/]?(\d{1,2})\s*$")


def normalizar_periodo(periodo_id: str) -> Tuple[int, int, str]:
    m = _PERIODO_RX.match(periodo_id or "")
    if not m:
        raise ValueError("periodo_id inválido; use 'YYYY-MM'")
    y, mth = int(m.group(1)), int(m.group(2))
    if y < 1900 or y > 3000 or not (1 <= mth <= 12):
        raise ValueError("periodo fuera de rango")
    return y, mth, f"{y:04d}-{mth:02d}"


def _parse_date(s: Optional[Union[str, date, datetime]]):
    if not s:
        return None
    if isinstance(s, date) and not isinstance(s, datetime):
        return s
    if isinstance(s, datetime):
        return s.date()

    s = str(s).strip()

    try:
        return date.fromisoformat(s)
    except Exception:
        pass

    try:
        s2 = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s2).date()
    except Exception:
        pass

    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue

    return None


def to_yyyy_mm_dd(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
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


def _parse_fecha_to_yyyy_mm_dd(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    try:
        if "-" in s and len(s) == 10 and s[2] == "-" and s[5] == "-":
            dt = datetime.strptime(s, "%d-%m-%Y")
            return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    try:
        dt = datetime.strptime(s[:10], "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None
