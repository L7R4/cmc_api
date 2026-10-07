"""`scripts/migrar_planillas_legacy.py`: trae a `uploads/planillas/` los PDF de las
planillas que servía la raíz del legacy y reescribe `avisos.ARCHIVO`.

Base de desarrollo con `commit` → `flush` y rollback al final; los PDF van a un
`tmp_path` (se redirige `PLANILLAS_DIR`), así no queda nada en el repo ni en la base.
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import Avisos
from app.modules.planillas import routes, service

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import migrar_planillas_legacy as script  # noqa: E402

PDF = b"%PDF-1.4\n% planilla de prueba\n"


@pytest.fixture
async def s(db, monkeypatch, tmp_path):
    monkeypatch.setattr(db, "commit", db.flush)
    monkeypatch.setattr(service, "PLANILLAS_DIR", tmp_path / "planillas")
    (tmp_path / "planillas").mkdir()
    try:
        yield db
    finally:
        await db.rollback()


async def _fila(s, archivo: str, aviso: str) -> Avisos:
    f = Avisos(AVISO=aviso, ARCHIVO=archivo, FECHA="28/01/2025", EXISTE="S", AVISO_PLANILLA="P")
    s.add(f)
    await s.flush()
    return f


def _origen(tmp_path: Path, archivos: dict[str, bytes]) -> Path:
    origen = tmp_path / "origen"
    origen.mkdir()
    for nombre, data in archivos.items():
        (origen / nombre).write_bytes(data)
    return origen


async def test_migra_respeta_acentos_y_saltea_las_ya_migradas(s, tmp_path):
    acentuada = await _fila(s, "TEST_Poder Judicial_de_la_Nación.pdf", "TEST PODER JUDICIAL")
    simple = await _fila(s, "TEST_IOSCOR.pdf", "TEST IOSCOR")
    ya = await _fila(s, "planillas/TEST_ya.pdf", "TEST YA MIGRADA")
    # En el hosting el nombre puede venir sin el acento (o en otra normalización).
    origen = _origen(tmp_path, {"TEST_Poder Judicial_de_la_Nacion.pdf": PDF, "test_ioscor.PDF": PDF})
    ids = {acentuada.ID, simple.ID, ya.ID}

    rep = await script.migrar(s, origen, aplicar=True, extras=False, solo_ids=ids)

    assert rep.ok and rep.backup is not None and rep.backup.exists()
    assert acentuada.ARCHIVO.startswith("planillas/") and simple.ARCHIVO.startswith("planillas/")
    assert ya.ARCHIVO == "planillas/TEST_ya.pdf"
    assert (service.PLANILLAS_DIR / acentuada.ARCHIVO.split("/", 1)[1]).read_bytes() == PDF
    # Lo que va a ver el médico: el link al endpoint propio, no a la raíz del legacy.
    assert routes._to_out(simple).url.startswith("/api/archivos/planillas/")
    backup = rep.backup.read_text(encoding="utf-8")
    assert f"WHERE ID = {acentuada.ID};" in backup and "TEST_Poder Judicial_de_la_Nación.pdf" in backup


async def test_modo_prueba_y_faltantes_no_tocan_nada(s, tmp_path):
    con_pdf = await _fila(s, "TEST_A.pdf", "TEST A")
    sin_pdf = await _fila(s, "TEST_B.pdf", "TEST B")
    origen = _origen(tmp_path, {"TEST_A.pdf": PDF})
    ids = {con_pdf.ID, sin_pdf.ID}

    # Prueba: informa y no escribe.
    rep = await script.migrar(s, origen, aplicar=False, extras=False, solo_ids=ids)
    assert [i.id for i in rep.faltantes] == [sin_pdf.ID]
    assert con_pdf.ARCHIVO == "TEST_A.pdf"

    # Aplicar con un faltante tampoco escribe nada: es todo o nada.
    rep = await script.migrar(s, origen, aplicar=True, extras=False, solo_ids=ids)
    assert not rep.ok and rep.backup is None
    assert con_pdf.ARCHIVO == "TEST_A.pdf"
    assert list(service.PLANILLAS_DIR.iterdir()) == []


async def test_no_pdf_no_se_aplica(s, tmp_path):
    fila = await _fila(s, "TEST_C.pdf", "TEST C")
    origen = _origen(tmp_path, {"TEST_C.pdf": b"<html>login</html>"})
    rep = await script.migrar(s, origen, aplicar=True, extras=False, solo_ids={fila.ID})
    assert [i.id for i in rep.no_pdf] == [fila.ID] and fila.ARCHIVO == "TEST_C.pdf"


async def test_extras_se_publican_una_sola_vez(s, tmp_path):
    origen = _origen(tmp_path, {nombre: PDF for _, nombre in script.EXTRAS})

    rep = await script.migrar(s, origen, aplicar=True, extras=True, solo_ids=set())
    assert rep.ok
    nuevas = (await s.execute(
        select(Avisos).where(Avisos.AVISO.in_([d for d, _ in script.EXTRAS]), Avisos.EXISTE == "S")
    )).scalars().all()
    assert len(nuevas) == 2 and all(n.ARCHIVO.startswith("planillas/") for n in nuevas)
    assert all(n.AVISO_PLANILLA == "P" for n in nuevas)

    # Segunda corrida: ya están publicadas, no se duplican.
    rep = await script.migrar(s, origen, aplicar=True, extras=True, solo_ids=set())
    assert all(i.nota == "ya publicada" for i in rep.items)
