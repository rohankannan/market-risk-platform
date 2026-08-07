"""Alembic environment: DATABASE_URL env var wins over alembic.ini."""
import os

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
url = os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url"))
target_metadata = None  # raw-SQL migrations; no autogenerate


def run_migrations_offline() -> None:
    context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = url
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
