"""Tope de tamaño de body (S2).

El riesgo que cubre no es exótico: `POST /api/valores_nm/actualizar_por_codigos`
acepta un array de items y no tenía techo. Un body de cientos de MB lo lee
Uvicorn a memoria y lo valida Pydantic entero. Con 2 workers, dos requests
concurrentes bajan la API — sin necesidad de credenciales, porque el límite se
evalúa antes que la autorización.
"""
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings


@pytest.fixture
def cliente(app):
    return TestClient(app, raise_server_exceptions=False)


def test_json_grande_da_413(cliente):
    """Un body por encima del límite se rechaza con 413, no con 401 ni 422.

    Importa que sea 413 y no otra cosa: el cliente tiene que poder distinguir
    "mandaste demasiado" de "mandaste mal", porque la solución es distinta
    (partir en lotes vs. corregir el payload).
    """
    grande = "x" * (settings.MAX_JSON_BODY_BYTES + 1024)
    r = cliente.post("/auth/login", content=grande, headers={"Content-Type": "application/json"})
    assert r.status_code == 413, r.text


def test_el_limite_corre_antes_que_la_autenticacion(cliente):
    """413 sin token, en una ruta que exige token.

    Es deliberado y es el punto del middleware: si el tope se evaluara después
    de autenticar, el body enorme ya estaría en memoria cuando se rechaza y el
    ataque funcionaría igual.
    """
    grande = "x" * (settings.MAX_JSON_BODY_BYTES + 1024)
    r = cliente.post(
        "/api/valores_nm/actualizar_por_codigos",
        content=grande,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413, r.text


def test_content_length_mentiroso_tambien_se_corta(cliente):
    """El header lo manda el cliente: no alcanza con creerle.

    Se manda `Content-Length` chico y un cuerpo grande. Sin el conteo real, el
    primer control lo dejaría pasar y el límite sería puramente decorativo.
    """
    grande = b"x" * (settings.MAX_JSON_BODY_BYTES + 1024)

    def cuerpo_por_partes():
        for i in range(0, len(grande), 65536):
            yield grande[i:i + 65536]

    # Sin Content-Length: httpx usa Transfer-Encoding: chunked.
    r = cliente.post(
        "/auth/login",
        content=cuerpo_por_partes(),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413, r.text


def test_un_json_normal_pasa(cliente):
    """El límite no puede estorbar al uso legítimo.

    Un login devuelve 401 o 422 según el body, pero NUNCA 413.
    """
    r = cliente.post("/auth/login", json={"nro_socio": 1, "password": "x"})
    assert r.status_code != 413, r.text


def test_el_limite_cubre_el_peor_caso_real():
    """1 MB tiene que seguir alcanzando para la carga masiva más grande.

    `ActualizarPorCodigosIn` lleva un item por código del nomenclador: 5.175
    códigos a ~75 bytes son ~390 KB. Si alguien baja el límite por debajo de
    eso, rompe la actualización de tarifario y el síntoma sería un 413 en una
    pantalla que antes andaba.
    """
    codigos_del_nomenclador = 5175
    bytes_por_item = 75
    peor_caso = codigos_del_nomenclador * bytes_por_item

    assert settings.MAX_JSON_BODY_BYTES >= peor_caso * 4, (
        f"El límite ({settings.MAX_JSON_BODY_BYTES} bytes) deja menos de 4x de "
        f"margen sobre la carga masiva del nomenclador (~{peor_caso} bytes)"
    )


def test_los_uploads_no_pasan_por_este_limite():
    """`multipart/form-data` queda exento: lo valida `app/common/uploads.py`.

    Sin la exención, subir un PDF de 5 MB —que el sistema acepta a propósito—
    daría 413.
    """
    from app.middleware.body_limit import _TIPOS_EXENTOS, _exento

    assert "multipart/form-data" in _TIPOS_EXENTOS
    assert _exento([(b"content-type", b"multipart/form-data; boundary=----abc")])
    assert not _exento([(b"content-type", b"application/json")])
    assert settings.MAX_UPLOAD_BYTES > settings.MAX_JSON_BODY_BYTES
