"""Ficha completa de una prestación (GET /prestaciones/{id}/ficha).

La cobertura de que la ruta esté en la allowlist de autorización la da
`test_autorizacion.py::test_todo_endpoint_declara_autorizacion` (rompe si falta
la línea en `SCOPES_POR_RUTA`). Acá se cubre el contrato del endpoint en sí:
404 con un id inexistente, y que `created_at` (la columna `created`, recién
mapeada — ver `app/db/models/cmc_facturacion.py`) llegue no nula en una fila
real, que es la base de la que dependen los listados ordenados por fecha de
carga.
"""
import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token


@pytest.fixture
def cliente(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def headers_admin():
    token = create_access_token(sub="1", uid=1, scopes=["facturacion:leer"], role="admin")
    return {"Authorization": f"Bearer {token}"}


def test_ficha_404_con_id_inexistente(cliente, headers_admin):
    r = cliente.get("/api/facturacion/prestaciones/999999999/ficha", headers=headers_admin)
    assert r.status_code == 404, r.text


def test_ficha_trae_created_at_de_una_fila_real(cliente, headers_admin):
    # Toma un id real del listado (ya autenticado y funcionando) en vez de pegarle
    # directo a la base: así el test no depende de una fila fija que puede
    # anularse o desaparecer entre corridas.
    listado = cliente.get(
        "/api/facturacion/prestaciones?limit=1", headers=headers_admin,
    )
    assert listado.status_code == 200, listado.text
    filas = listado.json()
    assert filas, "no hay ninguna prestación cargada en esta base de prueba"
    prestacion_id = filas[0]["id"]
    assert filas[0]["created_at"] is not None

    r = cliente.get(
        f"/api/facturacion/prestaciones/{prestacion_id}/ficha", headers=headers_admin,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["prestacion"]["id_detalle_prestaciones"] == prestacion_id
    assert body["prestacion"]["created_at"] is not None
