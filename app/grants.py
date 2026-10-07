"""Least-privilege grants for the app's runtime database role.

On Azure the schema is owned by the migration identity and the app connects as a separate
role (docs/AZURE_DEPLOYMENT.md §6). Only a table's owner can grant on it, so the migration job
runs this after `alembic upgrade head`:

    APP_DB_ROLE=id-governance-app python -m app.grants

Idempotent. No-op unless APP_DB_ROLE is set and the database is Postgres. The app role gets
read/write on the tables but can never change history: no UPDATE/DELETE/TRUNCATE on
audit_events and no writes to alembic_version.
"""
import sys

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app import config, db


def statements(role: str) -> list[str]:
    r = '"' + role.replace('"', '""') + '"'
    return [
        f"GRANT USAGE ON SCHEMA public TO {r}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {r}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {r}",
        f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM {r}",
        f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON alembic_version FROM {r}",
        # tables created by later migrations (run by this same owner role)
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {r}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {r}",
    ]


def apply(engine: Engine, role: str) -> bool:
    if engine.dialect.name != "postgresql":
        return False
    with engine.begin() as conn:
        for stmt in statements(role):
            conn.execute(text(stmt))
    return True


if __name__ == "__main__":
    if not config.APP_DB_ROLE:
        print("APP_DB_ROLE not set; no grants applied")
        sys.exit(0)
    print("grants applied" if apply(db.init_engine(), config.APP_DB_ROLE)
          else "not Postgres; grants skipped")
