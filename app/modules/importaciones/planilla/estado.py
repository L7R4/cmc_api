"""Estado de una importación en curso: avance y pedido de cancelación.

## Por qué en archivos

La API corre con varios workers (`UVICORN_WORKERS`) y el pedido de cancelar llega por
otra conexión, que puede caer en otro worker que el de la importación. Un diccionario en
memoria no se ve entre procesos. Los workers sí comparten el disco del contenedor, así que
el estado vive en archivos chicos en el directorio temporal:

    <tmp>/cmc_planilla/<lote>.estado     JSON con el avance; lo escribe la importación
    <tmp>/cmc_planilla/<lote>.cancelar   marca vacía; la crea el pedido de cancelar

No hace falta ningún cambio en la base. Si algún día la API corre en más de un contenedor,
esto deja de compartirse entre ellos: la importación igual sigue siendo todo o nada (el
commit es uno solo) y `GET /planilla/lotes/{lote}` cuenta lo grabado en la base.

## Qué garantiza

La marca se crea ANTES de que el cliente espere nada, y la importación la mira al empezar,
en cada paso y una vez más justo antes del commit. Así, cancelar en cualquier momento previo
al commit evita la carga, incluso si el pedido llegó antes que la importación. Pasado ese
último control ya no se puede frenar: el estado queda en `guardando` y después `grabada`.
"""
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Optional

DIRECTORIO = Path(tempfile.gettempdir()) / "cmc_planilla"
_LOTE = re.compile(r"^[0-9a-fA-F-]{8,40}$")
_VIGENCIA_S = 3600
_ACTUALIZAR_CADA_S = 0.25

# Estados que ya no cambian.
FINALES = ("grabada", "cancelada", "error")

_ultima_escritura: dict[str, float] = {}


def lote_valido(lote: str) -> bool:
    return bool(_LOTE.fullmatch(lote or ""))


def _ruta(lote: str, sufijo: str) -> Path:
    if not lote_valido(lote):  # el lote termina en un nombre de archivo
        raise ValueError("Lote inválido.")
    return DIRECTORIO / f"{lote}.{sufijo}"


def limpiar_viejos() -> None:
    """Borra los archivos de importaciones de hace más de una hora."""
    try:
        corte = time.time() - _VIGENCIA_S
        for f in DIRECTORIO.iterdir():
            try:
                if f.stat().st_mtime < corte:
                    f.unlink()
            except OSError:
                pass
    except OSError:
        pass


def pedir_cancelacion(lote: str) -> None:
    DIRECTORIO.mkdir(parents=True, exist_ok=True)
    _ruta(lote, "cancelar").touch()


def cancelacion_pedida(lote: str) -> bool:
    return _ruta(lote, "cancelar").exists()


def escribir(lote: str, estado: str, hecho: int = 0, total: int = 0, *, forzar: bool = False, detalle: str = "") -> None:
    """Escritura atómica (archivo temporal + reemplazo). Las de avance se espacian para
    no escribir una por fila; los cambios de estado se escriben siempre (`forzar`)."""
    ahora = time.monotonic()
    if not forzar and ahora - _ultima_escritura.get(lote, 0.0) < _ACTUALIZAR_CADA_S:
        return
    _ultima_escritura[lote] = ahora
    DIRECTORIO.mkdir(parents=True, exist_ok=True)
    destino = _ruta(lote, "estado")
    tmp = destino.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps({"estado": estado, "hecho": hecho, "total": total, "detalle": detalle, "ts": time.time()}),
        encoding="utf-8",
    )
    os.replace(tmp, destino)
    if estado in FINALES:
        _ultima_escritura.pop(lote, None)


def leer(lote: str) -> Optional[dict]:
    try:
        return json.loads(_ruta(lote, "estado").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
