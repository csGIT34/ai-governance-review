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


def test_catalog_models_endpoint(client, monkeypatch):
    from app.enrichment import azure_catalog, gcp_catalog
    monkeypatch.setattr(azure_catalog, "list_models", lambda region: {
        "models": [{"name": "gpt-4.1", "format": "OpenAI", "versions": ["2025-04-14"]}]})
    monkeypatch.setattr(gcp_catalog, "list_models", lambda publisher: {
        "models": [{"name": f"{publisher}-model", "versions": []}]})

    data = client.get("/api/catalog-models?csp=azure&region=eastus2,swedencentral").json()
    assert data["models"][0]["name"] == "gpt-4.1"

    data = client.get("/api/catalog-models?csp=gcp&publisher=anthropic").json()
    assert data["models"][0]["name"] == "anthropic-model"


def test_publisher_stored_and_used_by_enrich(client, monkeypatch):
    from app.enrichment import gcp_catalog
    resp = client.post("/reviews", data={
        "csp": "gcp", "service_name": "Vertex AI", "model_name": "claude-sonnet-4-5",
        "publisher": "anthropic",
    }, follow_redirects=False)
    rid = resp.headers["location"].rsplit("/", 1)[-1]
    assert _docs[rid]["publisher"] == "anthropic"

    seen = {}
    monkeypatch.setattr(gcp_catalog, "enrich", lambda name, publisher="google": (
        seen.update(publisher=publisher) or {"error": "stub"}))
    client.post(f"/reviews/{rid}/enrich", follow_redirects=False)
    assert seen["publisher"] == "anthropic"


STUB_HTML = b"<html>terms</html>"


def _stub_reference_docs(monkeypatch, pdf_ok=True, hashes_match=True):
    from app.enrichment import reference_docs
    monkeypatch.setattr(reference_docs, "fetch", lambda url: STUB_HTML)
    if pdf_ok:
        monkeypatch.setattr(reference_docs, "fetch_pdf", lambda url: b"%PDF-stub")
    else:
        monkeypatch.setattr(reference_docs, "fetch_pdf",
                            lambda url: (_ for _ in ()).throw(RuntimeError("no chromium")))
    stub_hash = reference_docs.text_hash(STUB_HTML) if hashes_match else "stale"
    for docs in reference_docs.DOCS.values():
        for d in docs:
            if "validated_hash" in d:
                monkeypatch.setitem(d, "validated_hash", stub_hash)


def test_reference_docs_saved_and_evidence_linked(client, monkeypatch):
    _stub_reference_docs(monkeypatch)
    rid = create(client)
    # pre-set evidence on C1 to confirm it is not overwritten
    client.post(f"/reviews/{rid}/checklist", data={
        "status__C1": "unreviewed", "notes__C1": "", "evidence__C1": "my-own-link",
    }, follow_redirects=False)

    resp = client.post(f"/reviews/{rid}/reference-docs", follow_redirects=False)
    assert resp.status_code == 303
    doc = _docs[rid]
    names = {a["name"] for a in doc["artifacts"]}
    assert "azure-openai-data-privacy.pdf" in names
    assert doc["checklist"]["C3"]["evidence"].startswith("azure-openai-data-privacy.pdf")
    assert doc["checklist"]["C1"]["evidence"] == "my-own-link"

    # curated standard positions auto-fill with provenance
    assert doc["checklist"]["C3"]["status"] == "pass"
    assert doc["checklist"]["C3"]["notes"].startswith("[auto]")
    assert doc["checklist"]["C4"]["status"] == "needs_info"  # acceptability stays human
    assert doc["checklist"]["E3"]["status"] == "pass"
    # items without a position stay unreviewed (evidence only)
    assert doc["checklist"]["C2"]["status"] == "unreviewed"
    assert doc["checklist"]["E2"]["status"] == "unreviewed"

    download = client.get(f"/reviews/{rid}/artifacts/azure-openai-data-privacy.pdf")
    assert download.content == b"%PDF-stub"
    assert download.headers["content-type"] == "application/pdf"


def test_reference_docs_changed_page_blocks_positions(client, monkeypatch):
    _stub_reference_docs(monkeypatch, hashes_match=False)
    rid = create(client)
    resp = client.post(f"/reviews/{rid}/reference-docs", follow_redirects=False)
    assert "changed" in unquote(resp.headers["location"])
    doc = _docs[rid]
    # standard position NOT applied; item flagged for re-validation instead
    assert doc["checklist"]["C3"]["status"] == "needs_info"
    assert "changed" in doc["checklist"]["C3"]["notes"]
    assert doc["checklist"]["C3"]["evidence"].startswith("azure-openai-data-privacy.pdf")


def test_reference_docs_html_fallback_when_pdf_fails(client, monkeypatch):
    _stub_reference_docs(monkeypatch, pdf_ok=False)
    rid = create(client)
    client.post(f"/reviews/{rid}/reference-docs", follow_redirects=False)
    doc = _docs[rid]
    names = {a["name"] for a in doc["artifacts"]}
    assert "azure-openai-data-privacy.html" in names
    assert doc["checklist"]["C3"]["status"] == "pass"  # positions unaffected by pdf failure


def test_enrich_saves_snapshot_artifact(client, monkeypatch):
    from app.enrichment import azure_catalog
    monkeypatch.setattr(azure_catalog, "enrich", lambda name, region: {
        "fetched_at": "2026-06-11T00:00:00+00:00", "source": "stub",
        "query": name, "match_count": 0, "models": [], "suggestions": []})
    rid = create(client)
    client.post(f"/reviews/{rid}/enrich", follow_redirects=False)
    doc = _docs[rid]
    assert any(a["name"].startswith("catalog-snapshot-") for a in doc["artifacts"])


def test_export(client):
    rid = create(client)
    data = client.get(f"/reviews/{rid}/export").json()
    assert data["model_name"] == "gpt-4.1"
    assert len(data["checklist"]) == 45 or len(data["checklist"]) > 40
