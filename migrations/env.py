from alembic import context
from sqlalchemy import create_engine

from app import config, models  # noqa: F401  (registers tables on Base.metadata)
from app.db import Base

url = context.config.get_main_option("sqlalchemy.url") or config.DATABASE_URL


def run_migrations_offline():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata,
                          render_as_batch=connection.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
