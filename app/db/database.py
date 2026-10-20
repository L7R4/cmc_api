"""Motor async, sessionmaker y la dependencia `get_db()`.

## Por qué `pool_pre_ping` dejó de ser opcional

MySQL cierra las conexiones ociosas al llegar a `wait_timeout` (8 horas por
defecto). El pool de SQLAlchemy no se entera: guarda un socket que del otro lado
ya no existe y lo entrega en el próximo checkout. La request que le toca falla
con "Lost connection to MySQL server" o "Event loop is closed".

Eso siempre estuvo (es E6 en `docs/api/AUDITORIA_SEGURIDAD.md`), pero antes solo
afectaba a los endpoints que consultaban la base. Desde que `enforce_authz` lee
`listado_medico.tokens_valid_from` para el corte de sesiones (A7/A12), **toda
request autenticada toca la base**: una conexión muerta ya no rompe un endpoint,
rompe la API entera hasta que el pool se renueva.

`pool_pre_ping=True` hace un `SELECT 1` antes de entregar la conexión y la
descarta si no responde. Cuesta un round-trip por checkout; el modo de falla que
evita es que la primera visita de cada mañana reciba un 500.

`pool_recycle` es el cinturón además de los tirantes: recicla toda conexión con
más de una hora, sin esperar a que el ping la encuentre muerta. Se elige un valor
cómodamente por debajo del `wait_timeout` típico.
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import settings

engine = create_async_engine(
    settings.MYSQL_URL,
    future=True,
    # `echo=True` estaba fijo. En producción eso escribe cada sentencia SQL con
    # sus parámetros al log — ruido, costo, y datos personales en un archivo que
    # no está pensado para contenerlos (documentos, CBU y CUIT viajan como
    # parámetros de bind). Ahora sigue al entorno.
    echo=not settings.IS_PRODUCTION,
    pool_pre_ping=settings.DB_POOL_PRE_PING,
    pool_recycle=3600,
    # La base de producción limita las conexiones por usuario (`max_user_connections`,
    # ~10) y es compartida con otras herramientas. Con el pool por defecto (5 + 10) la API
    # llegaba a ocupar todo el cupo y nadie más podía conectarse (error 1203). Tope por proceso: 5
    # por defecto; son configurables (DB_POOL_SIZE / DB_MAX_OVERFLOW) porque el total es
    # procesos x tope y hay que mantenerlo debajo del cupo.
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
)

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

Base = declarative_base()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
