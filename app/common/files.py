import os
import shutil
from datetime import datetime
from typing import Optional, Tuple

from fastapi import UploadFile
from fastapi.concurrency import run_in_threadpool

from app.db.models import Documento

UPLOAD_ROOT = "uploads"


async def save_upload_for_medico(medico_id: int, up: UploadFile) -> Optional[Tuple[Documento, str]]:
    """
    Guarda UploadFile en uploads/medicos/{id}/ y crea un Documento (sin commit).
    Devuelve (doc_model, rel_path) o None si no hay archivo.
    """
    if not up or not up.filename:
        return None

    dest_dir = os.path.join(UPLOAD_ROOT, "medicos", str(medico_id))
    os.makedirs(dest_dir, exist_ok=True)

    base = os.path.basename(up.filename)
    ts = int(datetime.now().timestamp())
    safe = "".join(ch for ch in base if ch.isalnum() or ch in "._- ").strip().replace(" ", "_")
    if not safe:
        safe = f"archivo_{ts}"
    filename = f"{ts}_{safe}"
    dest_path = os.path.join(dest_dir, filename)

    def _write_sync():
        up.file.seek(0)
        with open(dest_path, "wb") as f:
            shutil.copyfileobj(up.file, f, length=1024 * 1024)

    await run_in_threadpool(_write_sync)

    rel_path = dest_path.replace("\\", "/")
    size = os.path.getsize(dest_path)

    doc = Documento(
        medico_id=medico_id,
        label=None,
        original_name=base,
        filename=filename,
        content_type=getattr(up, "content_type", None),
        size=size,
        path=rel_path,
    )
    return doc, rel_path
