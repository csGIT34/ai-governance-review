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


def init_engine(url: str | None = None) -> Engine:
    global _engine
    url = url or config.DATABASE_URL
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        _engine = create_engine(url, **kwargs)
        event.listen(_engine, "connect",
                     lambda conn, _rec: conn.execute("PRAGMA foreign_keys=ON"))
    else:
        _engine = create_engine(url, pool_pre_ping=True)
    SessionLocal.configure(bind=_engine)
    return _engine


def engine() -> Engine | None:
    return _engine


def get_session():
    """FastAPI dependency. Routes commit explicitly; anything uncommitted is rolled back."""
    with SessionLocal() as session:
        yield session


__all__ = ["Base", "Session", "SessionLocal", "init_engine", "engine", "get_session"]
