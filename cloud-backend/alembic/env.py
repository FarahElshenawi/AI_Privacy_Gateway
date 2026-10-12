"""Alembic environment: the target database is DATABASE_URL (same as the app)."""
from alembic import context
from sqlalchemy import create_engine, pool

from app.db import Base, database_url

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = context.config.attributes.get("connection")
    if connectable is None:
        connectable = create_engine(database_url(), poolclass=pool.NullPool)
        with connectable.connect() as conn:
            context.configure(connection=conn, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    else:
        context.configure(connection=connectable, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
