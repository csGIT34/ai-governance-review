"""Smoke test: exercises the app end-to-end with in-memory stand-ins for
Cosmos and Blob, so it runs without Docker. Run: python -m pytest tests/"""
import io
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app import main
from app.db import blob, cosmos

_docs: dict[str, dict] = {}
_blobs: dict[str, bytes] = {}


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch):
    _docs.clear()
    _blobs.clear()
    monkeypatch.setattr(cosmos, "ensure_resources", lambda *a, **k: None)
    monkeypatch.setattr(cosmos, "list_reviews", lambda: sorted(
        _docs.values(), key=lambda d: d["created_at"], reverse=True))
    monkeypatch.setattr(cosmos, "get_review", lambda rid: _docs.get(rid))
    monkeypatch.setattr(cosmos, "save_review", lambda doc: _docs.__setitem__(doc["id"], doc))
    monkeypatch.setattr(blob, "upload_artifact", lambda rid, name, data: (
        _blobs.__setitem__(f"{rid}/{name}", data) or f"{rid}/{name}"))
    monkeypatch.setattr(blob, "download_artifact", lambda path: _blobs[path])


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def create(client) -> str:
    resp = client.post("/reviews", data={
        "csp": "azure", "service_name": "Azure OpenAI", "model_name": "gpt-4.1",
        "model_version": "2025-04-14", "regions": "eastus2",
        "requested_by": "app team", "justification": "need long context",
    }, follow_redirects=False)
    assert resp.status_code == 303
    return resp.headers["location"].rsplit("/", 1)[-1]


def test_create_and_view(client):
    rid = create(client)
    page = client.get(f"/reviews/{rid}")
    assert page.status_code == 200
    assert "gpt-4.1" in page.text
    assert "A1" in page.text and "M4" in page.text  # checklist rendered

    index = client.get("/")
    assert "gpt-4.1" in index.text


def test_checklist_save(client):
    rid = create(client)
    resp = client.post(f"/reviews/{rid}/checklist", data={
        "status__A1": "pass", "notes__A1": "documented in ticket", "evidence__A1": "JIRA-123",
    }, follow_redirects=False)
    assert resp.status_code == 303
    doc = _docs[rid]
    assert doc["checklist"]["A1"]["status"] == "pass"
    assert doc["checklist"]["A2"]["status"] == "unreviewed"


def test_approve_blocked_by_open_blockers(client):
    rid = create(client)
    resp = client.post(f"/reviews/{rid}/decision", data={
        "outcome": "approved", "approver": "manager",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert "Cannot" in unquote(resp.headers["location"])
    assert _docs[rid]["decision"] is None

    # conditional approval is allowed despite open blockers
    resp = client.post(f"/reviews/{rid}/decision", data={
        "outcome": "conditional", "approver": "manager",
        "conditions": "preview only in dev", "re_review_date": "2026-09-01",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert _docs[rid]["status"] == "conditional"


def test_approve_after_clearing_blockers(client):
    rid = create(client)
    from app import checklist
    blockers = {f"status__{i['id']}": "pass" for i in checklist.ITEMS
                if i["severity"] == "blocker"}
    client.post(f"/reviews/{rid}/checklist", data=blockers, follow_redirects=False)
    resp = client.post(f"/reviews/{rid}/decision", data={
        "outcome": "approved", "approver": "manager",
    }, follow_redirects=False)
    assert "Cannot fully approve" not in resp.headers["location"]
    assert _docs[rid]["status"] == "approved"


def test_artifact_roundtrip(client):
    rid = create(client)
    resp = client.post(f"/reviews/{rid}/artifacts",
                       files={"file": ("pricing.txt", io.BytesIO(b"token costs"), "text/plain")},
                       follow_redirects=False)
    assert resp.status_code == 303
    download = client.get(f"/reviews/{rid}/artifacts/pricing.txt")
    assert download.content == b"token costs"


def test_export(client):
    rid = create(client)
    data = client.get(f"/reviews/{rid}/export").json()
    assert data["model_name"] == "gpt-4.1"
    assert len(data["checklist"]) == 45 or len(data["checklist"]) > 40
