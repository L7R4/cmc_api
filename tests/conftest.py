"""Configuración común de los tests.

`Settings` no declara `env_file`: lee del entorno real, que en producción inyecta
docker-compose. Para correr los tests desde el host hay que cargar el `.env` a
mano **antes** de importar cualquier cosa de `app`, y apuntar `MYSQL_HOST` al
puerto publicado del contenedor (el hostname de red `mysql` no resuelve fuera de
la red de Docker).
"""
import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _cargar_env() -> None:
    env = RAIZ / ".env"
    if not env.exists():
        return
    for linea in env.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        # Los valores llevan comentarios al final: `COOKIE_SECURE=false  # ...`
        valor = valor.split("#", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(clave.strip(), valor)


_cargar_env()

# Solo si sigue apuntando al hostname de la red de Docker.
if os.environ.get("MYSQL_HOST") == "mysql":
    os.environ["MYSQL_HOST"] = os.environ.get("MYSQL_HOST_TESTS", "127.0.0.1")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import settings  # noqa: E402


@pytest.fixture(scope="session")
def app():
    from app.main import app as fastapi_app

    return fastapi_app


@pytest_asyncio.fixture
async def db():
    """Sesión contra la base de desarrollo.

    Engine propio con `NullPool` en vez del de `app.db.database`: pytest-asyncio
    abre un event loop nuevo por test, y una conexión aiomysql del pool creada en
    el loop anterior queda muerta en el siguiente ("'NoneType' object has no
    attribute 'send'" al reusarla). Sin pool, cada test abre y cierra la suya.

    Los tests de catálogo son de solo lectura: verifican que lo que hay en la
    base coincide con lo que declara el código. No crean ni borran nada.
    """
    engine = create_async_engine(settings.MYSQL_URL, poolclass=NullPool, echo=False)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def codigos_por_rol(db):
    """{rol: {codigos}} leído de `role_permission`."""
    filas = (await db.execute(text(
        "SELECT r.name, p.code FROM role_permission rp "
        "JOIN roles r ON r.id = rp.role_id "
        "JOIN permissions p ON p.id = rp.permission_id"
    ))).all()
    mapa: dict[str, set[str]] = {}
    for rol, code in filas:
        mapa.setdefault(rol, set()).add(code)
    return mapa
