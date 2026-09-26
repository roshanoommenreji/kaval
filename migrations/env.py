import logging

from alembic import context
from kaval_shared.db import database_url
from kaval_shared.models import Base
from sqlalchemy import engine_from_config, pool

config = context.config

# Configuration lives in pyproject.toml ([tool.alembic]); there is no alembic.ini.
# Logging is set here instead: Alembic's progress at INFO, everything else at WARNING.
logging.basicConfig(format="%(levelname)-5.5s [%(name)s] %(message)s", level=logging.WARNING)
logging.getLogger("alembic").setLevel(logging.INFO)

config.set_main_option("sqlalchemy.url", database_url())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
