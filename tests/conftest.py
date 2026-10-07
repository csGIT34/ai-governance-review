"""Test harness: SQLite in memory by default (no Docker needed); set
TEST_DATABASE_URL=postgresql+psycopg://... to run the same suite on Postgres.
Blob storage is an in-memory dict. Requests run as dev users (AUTH_MODE=dev)."""
import os

os.environ["AUTH_MODE"] = "dev"
os.environ.setdefault("TEST_DATABASE_URL", "sqlite://")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import blob, db  # noqa: E402
from app.models import AssessmentItem, AssessmentScopeNode  # noqa: E402

ADMIN, REVIEWER, PREPARER, VIEWER = ("alice@dev.local", "bob@dev.local", "carol@dev.local",
                                     "victor@dev.local")


@pytest.fixture(autouse=True)
def database():
    engine = db.init_engine(os.environ["TEST_DATABASE_URL"])
    db.Base.metadata.drop_all(engine)
    db.Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def blobs(monkeypatch):
    store: dict[str, bytes] = {}

    def upload(path, data, content_type):
        assert path not in store, "blob overwritten"
        store[path] = data
    monkeypatch.setattr(blob, "upload", upload)
    monkeypatch.setattr(blob, "download", lambda path: store[path])
    return store


@pytest.fixture
def session():
    with db.SessionLocal() as s:
        yield s


@pytest.fixture
def client():
    from app import main
    with TestClient(main.app) as c:
        c.cookies.set("dev_user", ADMIN)
        yield c


def as_user(client, upn):
    client.cookies.set("dev_user", upn)
    return client


def item_id(session, aid: int, ref: str) -> int:
    session.expire_all()
    return session.scalar(select(AssessmentItem.id).where(
        AssessmentItem.assessment_id == aid, AssessmentItem.ref == ref))


def node_id(session, aid: int, name: str | None = None) -> int:
    q = select(AssessmentScopeNode.id).where(AssessmentScopeNode.assessment_id == aid)
    if name:
        q = q.where(AssessmentScopeNode.name == name)
    return session.scalars(q.order_by(AssessmentScopeNode.id)).first()


def aid_from(resp) -> int:
    assert resp.status_code == 303, resp.text
    return int(resp.headers["location"].split("?")[0].rstrip("/").rsplit("/", 1)[-1])
