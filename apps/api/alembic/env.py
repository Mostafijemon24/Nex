from alembic import context
from sqlalchemy import pool

from app.db.base import Base
from app.db.session import engine
from app import models  # noqa: F401

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
