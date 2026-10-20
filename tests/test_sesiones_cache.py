"""Cache en memoria del corte de sesiones (`tokens_valid_from`)."""
import asyncio
import datetime

import pytest

from app.auth import sessions
from app.core.config import settings


class _FakeResult:
    def __init__(self, valor):
        self._v = valor

    def scalar_one_or_none(self):
        return self._v


class _FakeDb:
    """Cuenta cuántas veces se consultó el corte."""

    def __init__(self, corte):
        self.corte = corte
        self.consultas = 0

    async def execute(self, _stmt):
        self.consultas += 1
        return _FakeResult(self.corte)


@pytest.fixture(autouse=True)
def _limpio(monkeypatch):
    sessions.invalidar_cache_corte()
    monkeypatch.setattr(settings, "SESSION_CACHE_SECONDS", 30)
    yield
    sessions.invalidar_cache_corte()


def _corre(coro):
    return asyncio.run(coro)


def test_segunda_consulta_sale_del_cache():
    corte = datetime.datetime(2026, 10, 1, 12, 0, 0)
    db = _FakeDb(corte)
    ts = int(corte.replace(tzinfo=datetime.timezone.utc).timestamp())

    assert _corre(sessions.access_revocado(db, 7, ts - 10)) is True
    assert _corre(sessions.access_revocado(db, 7, ts + 10)) is False
    assert db.consultas == 1


def test_invalidar_fuerza_nueva_consulta():
    db = _FakeDb(None)
    _corre(sessions.access_revocado(db, 7, 1))
    sessions.invalidar_cache_corte(7)
    _corre(sessions.access_revocado(db, 7, 1))
    assert db.consultas == 2


def test_ttl_cero_desactiva_el_cache(monkeypatch):
    monkeypatch.setattr(settings, "SESSION_CACHE_SECONDS", 0)
    db = _FakeDb(None)
    _corre(sessions.access_revocado(db, 7, 1))
    _corre(sessions.access_revocado(db, 7, 1))
    assert db.consultas == 2


def test_sin_uid_o_iat_no_consulta():
    db = _FakeDb(None)
    assert _corre(sessions.access_revocado(db, None, 1)) is False
    assert _corre(sessions.access_revocado(db, 7, None)) is False
    assert db.consultas == 0


def test_vencido_vuelve_a_consultar(monkeypatch):
    db = _FakeDb(None)
    _corre(sessions.access_revocado(db, 7, 1))
    uid_val = sessions._cache_corte[7]
    sessions._cache_corte[7] = (uid_val[0], 0.0)  # ya venció
    _corre(sessions.access_revocado(db, 7, 1))
    assert db.consultas == 2
