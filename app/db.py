"""Engine/session setup. Call init_engine() once at startup (tests pass their own URL)."""
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import config


class Base(DeclarativeBase):
    pass


SessionLocal = sessionmaker(expire_on_commit=False)
_engine: Engine | None = None


# Token audience for Azure Database for PostgreSQL with Microsoft Entra authentication.
POSTGRES_ENTRA_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"


def build_engine(url: str) -> Engine:
    """Engine for any supported URL; also used by migrations (migrations/env.py)."""
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        eng = create_engine(url, **kwargs)
        event.listen(eng, "connect", lambda conn, _rec: conn.execute("PRAGMA foreign_keys=ON"))
        return eng
    if config.DATABASE_AUTH != "entra":
        return create_engine(url, pool_pre_ping=True)
    # Passwordless: the server checks the token only when a connection opens, so each new
    # connection asks for one (DefaultAzureCredential caches and refreshes it).
    eng = create_engine(url, pool_pre_ping=True)
    from azure.identity import DefaultAzureCredential
    credential = DefaultAzureCredential()

    @event.listens_for(eng, "do_connect")
    def _entra_password(dialect, conn_rec, cargs, cparams):
        cparams["password"] = credential.get_token(POSTGRES_ENTRA_SCOPE).token

    return eng


def init_engine(url: str | None = None) -> Engine:
    global _engine
    _engine = build_engine(url or config.DATABASE_URL)
    SessionLocal.configure(bind=_engine)
    return _engine


def engine() -> Engine | None:
    return _engine


def get_session():
    """FastAPI dependency. Routes commit explicitly; anything uncommitted is rolled back."""
    with SessionLocal() as session:
        yield session


__all__ = ["Base", "Session", "SessionLocal", "init_engine", "engine", "get_session"]
