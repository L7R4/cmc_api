"""Cookie `CMC_MEDICO`: marca que Caddy usa para sacar a los médicos del legacy.

Sin base: solo la regla de decisión y las cabeceras Set-Cookie que genera.
"""
from types import SimpleNamespace

import pytest
from fastapi import Response

from app.auth.router import COOKIE_MEDICO, _marcar_medico, es_medico_legacy


@pytest.mark.parametrize(
    "role, ingresar, esperado",
    [
        ("medico", "D", True),
        ("medico", "A", True),  # rol medico alcanza solo
        ("medico", None, True),
        ("admin", "D", True),  # INGRESAR='D' alcanza solo
        ("admin", " d ", True),  # la columna es CHAR: puede traer espacios/minúscula
        (None, "D", True),
        ("admin", "A", False),
        ("facturador", "F", False),
        ("liquidador", None, False),
        (None, None, False),
        ("admin", "", False),
    ],
)
def test_es_medico_legacy(role, ingresar, esperado):
    assert es_medico_legacy(role, ingresar) is esperado


def _set_cookie_headers(resp: Response) -> list[str]:
    return [v.decode() for k, v in resp.raw_headers if k == b"set-cookie"]


def test_medico_recibe_cookie():
    resp = Response()
    _marcar_medico(resp, "medico", SimpleNamespace(INGRESAR="D"), 3600)
    (h,) = _set_cookie_headers(resp)
    assert h.startswith(f"{COOKIE_MEDICO}=1")
    assert "HttpOnly" in h and "Path=/" in h and "Max-Age=3600" in h


def test_no_medico_se_le_borra_la_cookie():
    # Un usuario que cambió de médico a staff no puede quedarse con la marca vieja.
    resp = Response()
    _marcar_medico(resp, "admin", SimpleNamespace(INGRESAR="A"), 3600)
    (h,) = _set_cookie_headers(resp)
    assert h.startswith(f'{COOKIE_MEDICO}="";') or h.startswith(f"{COOKIE_MEDICO}=;")
    assert "Max-Age=0" in h
