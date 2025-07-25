from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings   # <-- importas aquí

# Aquí usas la URL construida en settings:
engine = create_async_engine(
    settings.MYSQL_URL,
    future=True,
    echo=True      # opcional: para ver en consola las consultas SQL
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