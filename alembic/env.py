from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, create_engine

# --- 1) Config Alembic y logging ---
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# --- 2) Carga URL desde settings ---
from app.core.config import settings
async_url = settings.MYSQL_URL # ésta es tu URL asíncrona: "mysql+aiomysql://user:pass@host/db"
sync_url = async_url.replace("mysql+aiomysql://", "mysql+pymysql://")# vamos a derivar una URL síncrona cambiando el driver:
config.set_main_option("sqlalchemy.url", sync_url)# Díselo a Alembic (aunque realmente no usaremos engine_from_config)

# --- 3) Metadata de tus modelos ---
from app.db.models import Base
target_metadata = Base.metadata

# def include_object(obj, name, type_, reflected, compare_to):
#     if type_ == "table":
#         return name in target_metadata.tables
#     return True

def run_migrations_offline() -> None:
    """Modo offline: genera SQL sin conectar."""
    context.configure(
        url=sync_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # include_object=include_object
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    """Modo online: conecta con el motor síncrono."""
    # Creamos un engine SÍNCRONO usando PyMySQL
    connectable = create_engine(
        sync_url,
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # include_object=include_object
        )
        with context.begin_transaction():
            context.run_migrations()

# --- 4) Arranque según modo ---
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
