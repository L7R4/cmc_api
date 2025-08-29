from typing import Tuple
import re

_PERIODO_RX = re.compile(r"^\s*(\d{4})[-/]?(\d{1,2})\s*$")

def normalizar_periodo(periodo_id: str) -> Tuple[int, int, str]:
    m = _PERIODO_RX.match(periodo_id or "")
    if not m:
        raise ValueError("periodo_id inválido; use 'YYYY-MM'")
    y, mth = int(m.group(1)), int(m.group(2))
    if y < 1900 or y > 3000 or not (1 <= mth <= 12):
        raise ValueError("periodo fuera de rango")
    return y, mth, f"{y:04d}-{mth:02d}"